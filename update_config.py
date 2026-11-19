import ipaddress
import json
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
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
GITHUB_MIRRORS = {"gh-proxy.com", "gh-proxy.org", "ghproxy.net"}
REQUEST_ERRORS = (OSError, ValueError, HTTPException, RecursionError)


class Source(NamedTuple):
    name: str
    url: str


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


def github_raw_mirror(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.hostname not in GITHUB_MIRRORS:
        return None
    match = re.fullmatch(r"/(?:https?:/{1,2})?raw\.githubusercontent\.com/+(.*)", parts.path)
    if not match:
        return None
    return urlunsplit(("https", "raw.githubusercontent.com", "/" + match[1], parts.query, ""))


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


def inspect_source(url: str) -> list[Source]:
    """Return catalog entries, or an empty list for a usable VOD config."""
    body, final_url = fetch(url, MAX_CONFIG_SIZE + 1)
    if len(body) > MAX_CONFIG_SIZE:
        raise ValueError("配置文件超过 4 MiB")
    config = parse_config(body)
    if "storeHouse" in config or "urls" in config:
        children = catalog_sources(config, final_url)
        if not children:
            raise ValueError("仓库目录没有可检查的地址")
        return children
    check_sites(config, final_url)
    return []


def probe(source: Source) -> tuple[str, list[Source]] | None:
    attempts = [source.url]
    mirror = github_raw_mirror(source.url)
    if mirror:
        attempts.append(mirror)

    failures = []
    for url in attempts:
        try:
            return url, inspect_source(url)
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

    print(f"跳过 {source.name}（{'；'.join(failures)}）")
    return None


def find_routes(seeds: list[Source]) -> list[Source]:
    pending = [((index,), source) for index, source in enumerate(seeds)]
    checked = {}
    positions = {}
    usable = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        for _ in range(MAX_DEPTH + 1):
            batch = []
            for position, source in sorted(pending):
                if source.url in positions and positions[source.url] <= position:
                    continue
                positions[source.url] = position
                batch.append((position, source))
            if not batch:
                break

            new = {source.url: source for _, source in batch if source.url not in checked}
            requests = list(new.values())[:MAX_URLS - len(checked)]
            for source, result in zip(requests, executor.map(probe, requests)):
                checked[source.url] = result

            pending = []
            for position, source in batch:
                result = checked.get(source.url)
                if result is None:
                    continue
                url, children = result
                if children:
                    pending.extend((position + (index,), child) for index, child in enumerate(children))
                else:
                    usable.append((position, Source(source.name, url)))

    unique = {}
    for _, source in sorted(usable):
        unique.setdefault(source.url, source)
    print(f"检查了 {len(checked)} 个目录或线路地址，{len(unique)} 条线路通过检查。")
    return list(unique.values())[:MAX_ROUTES]


def update_config(seeds_path: Path, output_path: Path) -> int:
    seeds = catalog_sources(parse_config(seeds_path.read_bytes()))
    if not seeds:
        raise ValueError("sources.json 缺少有效的候选目录")

    require_public_host.cache_clear()
    jar_available.cache_clear()
    routes = find_routes(seeds)
    if not routes:
        print("没有线路通过检查，保留上一次发布的配置。", file=sys.stderr)
        return 1

    config = {"urls": [source._asdict() for source in routes]}
    payload = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if output_path.is_file() and output_path.read_bytes() == payload:
        print(f"{output_path.name} 已是最新配置。")
        return 0

    with NamedTemporaryFile(dir=output_path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(payload)
            file.close()
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"已生成 {output_path.name}，首选线路为 {routes[0].name}。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(update_config(DIRECTORY / "sources.json", DIRECTORY / "tv.json"))
    except (OSError, ValueError) as error:
        sys.exit(f"更新失败：{error}")
