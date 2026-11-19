import ipaddress
import json
import os
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import NamedTuple
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DIRECTORY = Path(__file__).resolve().parent
MAX_CONFIG_SIZE = 4 * 1024 * 1024
MAX_DEPTH = 4
MAX_URLS = 160
MAX_ROUTES = 8
REQUEST_TIMEOUT = 7

JSON_STRING = r'"(?:\\[\s\S]|[^"\\])*"'
COMMENTS = re.compile(JSON_STRING + r'|//[^\r\n]*|/\*[\s\S]*?\*/')
TRAILING_COMMAS = re.compile(JSON_STRING + r'|,(?=\s*[}\]])')
EMBEDDED_URL = re.compile(r'(https?://[^/]+/)(https?://.+)')
GITHUB_RAW_HOST = "raw.githubusercontent.com"
GITHUB_PROXY_PATH = re.compile(
    r"/+(?:https?:/{1,2})?raw\.githubusercontent\.com/+(.*)", re.IGNORECASE
)
GITHUB_BRANCH_URL = re.compile(r"^(https://raw\.githubusercontent\.com/[^/]+/[^/]+)/refs/heads/")
README_LINKS_START = "<!-- tv-config-links:start -->"
README_LINKS_END = "<!-- tv-config-links:end -->"
REQUEST_ERRORS = (OSError, ValueError, HTTPException, RecursionError)


class Source(NamedTuple):
    name: str
    url: str


class CheckedSource(NamedTuple):
    url: str
    children: list[Source]
    digest: bytes


def resolve_url(value: str, base: str = "") -> str:
    value = value.strip()
    if not value or "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError("地址格式无效")

    if base and not urlsplit(value).scheme:
        if not any(char in value.split("?", 1)[0] for char in "/."):
            raise ValueError("缺少线路地址")
        embedded = EMBEDDED_URL.fullmatch(base)
        if embedded and not value.startswith("//"):
            value = embedded[1] + urljoin(embedded[2], value)
        else:
            value = urljoin(base, value)

    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("需要 HTTP(S) 地址")
    if parts.username is not None or parts.password is not None:
        raise ValueError("地址包含凭据")

    host = parts.hostname.encode("idna").decode("ascii")
    if ":" in host:
        ipaddress.IPv6Address(host)
        netloc = f"[{host}]"
    elif re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        netloc = host
    else:
        raise ValueError("主机名无效")
    if parts.port is not None:
        netloc += f":{parts.port}"

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~[]")
    return urlunsplit((parts.scheme, netloc, path, query, ""))


@lru_cache(maxsize=512)
def require_public_host(host: str) -> None:
    if "." not in host and ":" not in host:
        raise ValueError("地址不在公网")
    addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for _, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise ValueError("地址不在公网")


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = resolve_url(newurl)
        require_public_host(urlsplit(target).hostname)
        return super().redirect_request(req, fp, code, msg, headers, target)


OPENER = build_opener(PublicRedirects())


def fetch(url: str, limit: int, *, prefix_only: bool = False) -> tuple[bytes, str]:
    require_public_host(urlsplit(url).hostname)
    headers = {"User-Agent": "okhttp/4.12.0", "Cache-Control": "no-cache"}
    if prefix_only:
        headers["Range"] = f"bytes=0-{limit - 1}"
    with OPENER.open(Request(url, headers=headers), timeout=REQUEST_TIMEOUT) as response:
        return response.read(limit), response.geturl()


def parse_config(body: bytes) -> dict:
    text = body.decode("utf-8-sig")
    text = COMMENTS.sub(lambda match: " " if match[0].startswith("/") else match[0], text)
    text = TRAILING_COMMAS.sub(lambda match: "" if match[0] == "," else match[0], text)
    config = json.loads(text)
    if not isinstance(config, dict):
        raise ValueError("配置需要是 JSON 对象")
    return config


def catalog_sources(config: dict, base: str = "") -> list[Source]:
    sources = []
    for array_key, name_key, url_key in (
        ("storeHouse", "sourceName", "sourceUrl"),
        ("urls", "name", "url"),
    ):
        entries = config.get(array_key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get(url_key), str):
                continue
            try:
                url = resolve_url(entry[url_key], base)
            except ValueError:
                continue
            name = entry.get(name_key)
            label = " ".join(name.split())[:80] if isinstance(name, str) else ""
            sources.append(Source(label or urlsplit(url).hostname, url))
    return sources


def github_raw_url(url: str) -> str | None:
    parts = urlsplit(url)
    path = parts.path
    if parts.hostname != GITHUB_RAW_HOST:
        match = GITHUB_PROXY_PATH.fullmatch(path)
        if not match:
            return None
        path = "/" + match[1]
    if len(path.strip("/").split("/", 3)) < 4:
        return None
    return urlunsplit(("https", GITHUB_RAW_HOST, path, parts.query, ""))


def source_key(url: str) -> str:
    raw = github_raw_url(url)
    return GITHUB_BRANCH_URL.sub(r"\1/", raw) if raw else url


def configured_proxy_prefixes(config: dict) -> tuple[str, ...]:
    entries = config.get("githubProxyPrefixes", [])
    if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
        raise ValueError("githubProxyPrefixes 需要是 HTTPS 前缀数组")
    prefixes = []
    for entry in entries:
        url = resolve_url(entry)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.path != "/" or parts.query:
            raise ValueError("加速前缀需要是 HTTPS 站点根地址")
        prefixes.append(url)
    return tuple(dict.fromkeys(prefixes))


@lru_cache(maxsize=512)
def jar_available(url: str) -> bool:
    try:
        body, _ = fetch(url, 4, prefix_only=True)
    except REQUEST_ERRORS:
        return False
    return body == b"PK\x03\x04"


def check_sites(config: dict, base: str) -> None:
    sites = config.get("sites")
    if not isinstance(sites, list):
        raise ValueError("缺少 sites 列表")
    sites = [
        site
        for site in sites
        if isinstance(site, dict)
        and all(isinstance(site.get(key), str) and site[key].strip() for key in ("key", "name", "api"))
    ]
    if not sites:
        raise ValueError("没有有效的点播站点")

    jars = [config.get("spider"), *(site.get("jar") for site in sites)]
    jars = [jar for jar in jars if isinstance(jar, str) and jar.strip()]
    if not jars:
        if all(site["api"].strip().startswith("csp_") for site in sites):
            raise ValueError("点播站点缺少 Spider/JAR")
        return

    for jar in dict.fromkeys(jars):
        try:
            url = resolve_url(jar.split(";md5;", 1)[0], base)
        except ValueError:
            continue
        if jar_available(url):
            return
    raise ValueError("声明的 Spider/JAR 均未通过下载检查")


def inspect_source(url: str, expected_digest: bytes | None = None) -> CheckedSource:
    body, final_url = fetch(url, MAX_CONFIG_SIZE + 1)
    if len(body) > MAX_CONFIG_SIZE:
        raise ValueError("配置文件超过 4 MiB")
    if (
        urlsplit(url).hostname != GITHUB_RAW_HOST
        and github_raw_url(url)
        and urlsplit(final_url).hostname == GITHUB_RAW_HOST
    ):
        raise ValueError("加速地址跳转到了 GitHub Raw")
    digest = sha256(body).digest()
    if expected_digest is not None and digest != expected_digest:
        raise ValueError("返回内容与原文件版本不一致")
    config = parse_config(body)
    if "storeHouse" in config or "urls" in config:
        children = catalog_sources(config, final_url)
        if not children:
            raise ValueError("仓库目录没有可检查的地址")
    else:
        check_sites(config, final_url)
        children = []
    return CheckedSource(url, children, digest)


def try_source(
    url: str, failures: list[str], expected_digest: bytes | None = None
) -> CheckedSource | None:
    try:
        return inspect_source(url, expected_digest)
    except HTTPError as error:
        reason = f"HTTP {error.code}"
    except (UnicodeError, json.JSONDecodeError):
        reason = "响应不是可解析的 JSON"
    except (OSError, HTTPException):
        reason = "连接失败或超时"
    except RecursionError:
        reason = "JSON 嵌套过深"
    except ValueError as error:
        reason = str(error)
    failures.append(f"{urlsplit(url).hostname}: {reason}")
    return None


def probe(source: Source, proxy_prefixes: tuple[str, ...]) -> CheckedSource | None:
    failures = []
    raw = github_raw_url(source.url)
    direct = try_source(raw or source.url, failures)
    if raw and (direct is None or not direct.children):
        expected = direct.digest if direct else None
        candidates = [prefix + raw for prefix in proxy_prefixes]
        if source.url != raw:
            candidates.append(source.url)
        for url in dict.fromkeys(candidates):
            accelerated = try_source(url, failures, expected)
            if accelerated:
                return accelerated

    if direct is None:
        print(f"跳过 {source.name}（{'；'.join(failures)}）")
    return direct


def find_routes(seeds: list[Source], proxy_prefixes: tuple[str, ...] = ()) -> list[Source]:
    pending = [((index,), source) for index, source in enumerate(seeds)]
    checked = {}
    positions = {}
    usable = []
    check = partial(probe, proxy_prefixes=proxy_prefixes)

    with ThreadPoolExecutor(max_workers=10) as executor:
        for _ in range(MAX_DEPTH + 1):
            batch = []
            for position, source in sorted(pending):
                key = source_key(source.url)
                if key in positions and positions[key] <= position:
                    continue
                positions[key] = position
                batch.append((position, key, source))
            if not batch:
                break

            new = [(key, source) for _, key, source in batch if key not in checked]
            requests = new[:MAX_URLS - len(checked)]
            results = executor.map(check, (source for _, source in requests))
            for (key, _), result in zip(requests, results):
                checked[key] = result

            pending = []
            for position, key, source in batch:
                result = checked.get(key)
                if result is None:
                    continue
                if result.children:
                    pending.extend(
                        (position + (index,), child) for index, child in enumerate(result.children)
                    )
                else:
                    usable.append((position, key, Source(source.name, result.url)))

    unique = {}
    for _, key, source in sorted(usable):
        unique.setdefault(key, source)
    print(f"检查了 {len(checked)} 个目录或线路地址，{len(unique)} 条线路通过检查。")
    return list(unique.values())[:MAX_ROUTES]


def read_published_routes(payload: bytes) -> list[Source]:
    try:
        entries = parse_config(payload).get("urls")
    except (ValueError, RecursionError):
        return []
    if not isinstance(entries, list):
        return []
    routes = catalog_sources({"urls": entries})
    return routes if len(routes) == len(entries) else []


def published_route_error(source: Source) -> str | None:
    failures = []
    result = try_source(source.url, failures)
    if result is None:
        return "；".join(failures)
    if result.children:
        return "配置地址变成了仓库目录"
    return None


def check_published_routes(routes: list[Source]) -> list[Source]:
    with ThreadPoolExecutor(max_workers=10) as executor:
        retry = [
            source for source, error in zip(routes, executor.map(published_route_error, routes))
            if error is not None
        ]
        if not retry:
            return routes

        jar_available.cache_clear()
        print(f"有 {len(retry)} 条已发布线路首次检查失败，开始复查。")
        failed = set()
        for source, error in zip(retry, executor.map(published_route_error, retry)):
            if error is not None:
                failed.add(source)
                print(f"已发布线路失效 {source.name}（{error}）")
    return [source for source in routes if source not in failed]


def readme_with_links(
    readme: str, public_url: str, proxy_prefixes: tuple[str, ...], published_digest: bytes | None
) -> str:
    head, start, tail = readme.partition(README_LINKS_START)
    _, end, rest = tail.partition(README_LINKS_END)
    if not start or not end:
        raise ValueError("README 缺少配置链接区的标记")

    links = [("GitHub Raw 固定入口", public_url)]
    if published_digest is not None:
        for prefix in proxy_prefixes:
            url = prefix + public_url
            failures = []
            result = try_source(url, failures, published_digest)
            if result and result.children:
                links.append((f"{urlsplit(prefix).hostname} 加速入口", url))
            elif failures:
                print(f"README 加速入口未通过（{'；'.join(failures)}）")
    newline = "\r\n" if "\r\n" in readme else "\n"
    blocks = [newline.join((f"**{label}**", "", "```text", url, "```")) for label, url in links]
    return head + (newline * 2).join((start, *blocks, end)) + rest


def write_if_changed(path: Path, payload: bytes) -> bool:
    if path.is_file() and path.read_bytes() == payload:
        return False
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(payload)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def update_config(
    seeds_path: Path, output_path: Path, public_url: str | None = None, *, check_existing: bool = False
) -> int:
    settings = parse_config(seeds_path.read_bytes())
    seeds = catalog_sources(settings)
    if not seeds:
        raise ValueError("sources.json 缺少有效的候选目录")
    proxy_prefixes = configured_proxy_prefixes(settings)

    published_payload = output_path.read_bytes() if output_path.is_file() else None
    require_public_host.cache_clear()
    jar_available.cache_clear()
    published = read_published_routes(published_payload) if check_existing and published_payload else []
    routes = check_published_routes(published) if published else []
    if published_payload is not None and published and len(routes) == len(published):
        print(f"已发布的 {len(routes)} 条线路均通过检查，跳过上游抓取。")
        payload = published_payload
    else:
        if check_existing:
            jar_available.cache_clear()
            print("开始从上游查找可用线路。" if published else "缺少有效的已发布配置，开始从上游查找线路。")
        discovered = find_routes(seeds, proxy_prefixes)
        if not discovered and routes:
            print(f"上游没有新选出的线路，继续使用 {len(routes)} 条通过检查的现有线路。")
        routes = discovered or routes
        if not routes:
            print("没有线路通过检查，保留上一次发布的配置。", file=sys.stderr)
            return 1

        config = {"urls": [source._asdict() for source in routes]}
        payload = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    if public_url:
        # Check the published version before replacing the checkout's tv.json.
        published_digest = sha256(published_payload).digest() if published_payload is not None else None
        readme_path = output_path.with_name("README.md")
        readme = readme_path.read_bytes().decode("utf-8")
        readme = readme_with_links(readme, resolve_url(public_url), proxy_prefixes, published_digest)
        if write_if_changed(readme_path, readme.encode("utf-8")):
            print("已更新 README 配置地址。")

    if write_if_changed(output_path, payload):
        print(f"已生成 {output_path.name}，首选线路为 {routes[0].name}。")
    else:
        print(f"{output_path.name} 已是最新配置。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(
            update_config(
                DIRECTORY / "sources.json",
                DIRECTORY / "tv.json",
                os.environ.get("TV_CONFIG_URL"),
                check_existing=os.environ.get("GITHUB_EVENT_NAME") == "schedule",
            )
        )
    except (OSError, ValueError) as error:
        sys.exit(f"更新失败：{error}")
