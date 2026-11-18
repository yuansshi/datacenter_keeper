"""Read pyATS testbed YAML files into vendor-neutral specs.

Supports the parts of the pyATS testbed schema that matter for inventory:
``extends`` (recursive, merged base-first like pyATS), ``testbed.custom``/``testbed.servers``,
``devices.<name>.{type, os, platform, alias, connections, credentials, custom}``, and ``topology``.

Conventions understood out of the box (all optional, all overridable per device with
``custom.labkeeper``):

* Meraki devices: ``custom.meraki_device.{serial, model, location, stack}``
* Rack placement: ``location: B1_C03_U20`` -> rack ``B1_C03``, U5 (pattern is configurable)
* Logical stacks: ``chassis_type: stack`` groups members that carry ``stack: <name>``
* Consoles: telnet connections, connections named ``*console*``, optional ``proxy:`` jump host

A file that no other file extends is a *testbed*; a file that is extended is a *base* (a building
block such as a switch stack or a jump host definition).
"""

from __future__ import annotations

import copy
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..secrets_store import ConnectionPlan, ProxyPlan, redact, resolve_secret

LOOPBACKS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates unknown tags (``!include`` etc.) instead of failing the file."""


def _unknown_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)  # type: ignore[arg-type]


_Loader.add_multi_constructor("!", _unknown_tag)
_Loader.add_multi_constructor("tag:", _unknown_tag)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class TestbedFileError(Exception):
    pass


@dataclass
class LoadedFile:
    path: Path
    data: dict[str, Any]  # fully merged
    own: dict[str, Any]  # just this file
    origins: dict[str, Path]  # device name -> file that first defined it
    extends: list[Path]


class TestbedLoader:
    """Loads files with ``extends`` resolved. Only files inside ``roots`` may be loaded."""

    def __init__(self, roots: list[Path]):
        self.roots = [r.resolve() for r in roots]
        self._cache: dict[Path, LoadedFile] = {}

    def _check_inside_roots(self, path: Path) -> None:
        if not any(path == r or path.is_relative_to(r) for r in self.roots):
            raise TestbedFileError(f"{path}: outside the configured testbed folders")

    def load(self, path: Path, _stack: tuple[Path, ...] = ()) -> LoadedFile:
        path = path.resolve()
        if path in self._cache:
            return self._cache[path]
        self._check_inside_roots(path)
        if path in _stack:
            chain = " -> ".join(p.name for p in (*_stack, path))
            raise TestbedFileError(f"extends cycle: {chain}")
        if not path.is_file():
            raise TestbedFileError(f"{path}: file not found")
        try:
            raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
        except yaml.YAMLError as exc:
            raise TestbedFileError(f"{path.name}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise TestbedFileError(f"{path.name}: top level must be a mapping")

        extends = raw.get("extends") or []
        if isinstance(extends, str):
            extends = [extends]
        merged: dict[str, Any] = {}
        origins: dict[str, Path] = {}
        ext_paths: list[Path] = []
        for ext in extends:
            ext_path = (path.parent / str(ext)).resolve()
            ext_paths.append(ext_path)
            base = self.load(ext_path, (*_stack, path))
            merged = deep_merge(merged, base.data)
            for name, origin in base.origins.items():
                origins.setdefault(name, origin)
        own = {k: v for k, v in raw.items() if k != "extends"}
        merged = deep_merge(merged, own)
        for name in (own.get("devices") or {}) or {}:
            origins.setdefault(str(name), path)
        loaded = LoadedFile(path=path, data=merged, own=own, origins=origins, extends=ext_paths)
        self._cache[path] = loaded
        return loaded

    def raw_extends(self, path: Path) -> list[Path]:
        """Cheap pass used to build the extends graph without merging."""
        try:
            raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
        except Exception:
            return []
        if not isinstance(raw, dict):
            return []
        ext = raw.get("extends") or []
        if isinstance(ext, str):
            ext = [ext]
        return [(path.parent / str(e)).resolve() for e in ext]


# ---------------------------------------------------------------------------------------------------
# Normalised specs
# ---------------------------------------------------------------------------------------------------

ROLE_BY_TYPE = {
    "meraki-ms": "switch",
    "meraki-mr": "access_point",
    "meraki-mv": "camera",
    "meraki-mx": "security_appliance",
    "meraki-mg": "cellular_gateway",
    "meraki-mt": "sensor",
    "switch": "switch",
    "router": "router",
    "firewall": "security_appliance",
    "ap": "access_point",
    "access_point": "access_point",
    "camera": "camera",
    "sensor": "sensor",
    "rpi": "jump_host",
    "linux": "server",
    "server": "server",
    "ixia": "traffic_generator",
    "ixia-rest": "traffic_generator",
    "spirent": "traffic_generator",
    "trex": "traffic_generator",
    "terminal_server": "terminal_server",
}
SWITCH_PLATFORMS = re.compile(r"^(c9[0-9]{3}|cat9k|cat3k|cat2k|c3[0-9]{3}|nexus|n[0-9]k|ms[0-9]{2,3}|ex[0-9]+|qfx|dcs-)", re.I)


@dataclass
class DeviceSpec:
    key: str
    name: str
    origin: Path
    type: str | None = None
    os: str | None = None
    platform: str | None = None
    alias: str | None = None
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    role: str = "other"
    mgmt_ip: str | None = None
    is_shared: bool = False
    is_virtual: bool = False
    stack_name: str | None = None
    org_id: str | None = None
    provider: str | None = None
    location: str | None = None
    rack: str | None = None
    rack_position: int | None = None
    u_height: int = 1
    network: str | None = None
    tags: list[str] = field(default_factory=list)
    connections: dict[str, Any] = field(default_factory=dict)  # redacted
    custom: dict[str, Any] = field(default_factory=dict)  # redacted
    plans: dict[str, ConnectionPlan] = field(default_factory=dict)  # contains secrets, memory only


@dataclass
class TestbedSpec:
    key: str
    name: str
    path: Path
    root: Path
    group: str | None
    org_id: str | None
    custom: dict[str, Any]
    topology: dict[str, Any]
    device_keys: list[str]
    description: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class FileReport:
    path: Path
    root: Path
    kind: str  # testbed | base | invalid
    error: str | None = None
    device_count: int = 0
    testbed_key: str | None = None
    mtime: float | None = None


@dataclass
class ScanResult:
    devices: dict[str, DeviceSpec]
    testbeds: list[TestbedSpec]
    files: list[FileReport]
    roots: list[Path]


def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


MERAKI_SERIAL_RE = re.compile(r"^Q[0-9A-Z]{3}-[0-9A-Z]{4}-[0-9A-Z]{4}$")


def _vendor_for(dtype: str | None, os_name: str | None, custom: dict[str, Any]) -> str | None:
    explicit = custom.get("vendor")
    if explicit:
        return str(explicit)
    t = (dtype or "").lower()
    o = (os_name or "").lower()
    if t.startswith("meraki"):
        return "Cisco Meraki"
    if o in {"iosxe", "ios", "nxos", "iosxr", "asa", "viptela", "aireos"} or t in {"iosxe", "c9300"}:
        return "Cisco"
    if o in {"junos"}:
        return "Juniper"
    if o in {"eos"}:
        return "Arista"
    if t.startswith("ixia") or o == "hlapi":
        return "Keysight"
    if t == "rpi":
        return "Raspberry Pi"
    if o == "linux" or t == "linux":
        return "Linux"
    return None


def _role_for(dtype: str | None, platform: str | None, model: str | None, override: str | None) -> str:
    if override:
        return override
    t = (dtype or "").lower()
    if t in ROLE_BY_TYPE:
        return ROLE_BY_TYPE[t]
    for candidate in (platform, model, dtype):
        if candidate and SWITCH_PLATFORMS.match(str(candidate)):
            return "switch"
    return "other"


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_location(location: str | None, pattern: re.Pattern[str]) -> tuple[str | None, int | None]:
    if not location:
        return None, None
    loc = str(location).strip()
    m = pattern.match(loc)
    if m:
        return m.group("rack"), _as_int(m.group("position"))
    return loc, None


class Normalizer:
    def __init__(self, shared_types: list[str], location_pattern: str):
        self.shared_types = {t.lower() for t in shared_types}
        self.location_re = re.compile(location_pattern)

    # -- connections & jump hosts ------------------------------------------------------------------
    def _credentials(self, device: dict[str, Any], testbed: dict[str, Any]) -> tuple[str | None, Any]:
        for creds in (device.get("credentials"), (testbed.get("testbed") or {}).get("credentials")):
            if isinstance(creds, dict):
                default = creds.get("default") or {}
                if isinstance(default, dict) and (default.get("username") or default.get("password")):
                    return default.get("username"), default.get("password")
        return None, None

    def _resolve_proxy(self, name: str, merged: dict[str, Any]) -> tuple[ProxyPlan | None, str | None]:
        """A jump host may be a device, a ``testbed.servers`` entry, or both (credentials in one,
        address in the other). Combine whatever is available; the device's SSH address wins."""
        devices = merged.get("devices") or {}
        servers = (merged.get("testbed") or {}).get("servers") or {}
        candidates = list(dict.fromkeys([name, name.replace("-", "_"), name.replace("_", "-")]))
        for cand in candidates:
            dev = devices.get(cand) if isinstance(devices.get(cand), dict) else None
            srv = servers.get(cand) if isinstance(servers.get(cand), dict) else None
            if dev is None and srv is None:
                continue
            host = port = user = password = None
            if dev is not None:
                for cname, c in (dev.get("connections") or {}).items():
                    if cname == "defaults" or not isinstance(c, dict):
                        continue
                    if str(c.get("protocol", "")).lower() == "ssh" and c.get("ip"):
                        host = str(c.get("ip"))
                        port = _as_int(c.get("port"))
                        user = c.get("username")
                        password = c.get("password")
                        break
                d_user, d_pass = self._credentials(dev, merged)
                user, password = user or d_user, password or d_pass
            if srv is not None:
                creds = (srv.get("credentials") or {}).get("default") or {}
                host = host or (str(srv.get("address") or srv.get("server") or "") or None)
                port = port or _as_int(srv.get("port"))
                user = user or creds.get("username")
                password = password or creds.get("password")
            if not host:
                continue
            return (
                ProxyPlan(
                    name=cand,
                    host=host,
                    port=port or 22,
                    username=str(user) if user else None,
                    password=resolve_secret(password) or None,
                    password_encrypted=bool(password and str(password).startswith("%ENC{")),
                ),
                None,
            )
        return None, f"jump host '{name}' is not defined in this testbed"

    def _connections(
        self, device: dict[str, Any], merged: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, ConnectionPlan], str | None]:
        conns = device.get("connections") or {}
        public: dict[str, Any] = {}
        plans: dict[str, ConnectionPlan] = {}
        mgmt_ip: str | None = None
        if not isinstance(conns, dict):
            return public, plans, mgmt_ip
        for cname, c in conns.items():
            if cname == "defaults" or not isinstance(c, dict):
                continue
            protocol = str(c.get("protocol") or "").lower() or "unknown"
            host = c.get("ip") or c.get("host")
            host = str(host) if host is not None else None
            port = _as_int(c.get("port"))
            proxy_name = c.get("proxy")
            is_console = (
                "console" in str(cname).lower()
                or protocol == "telnet"
                or bool(c.get("console_server_ip"))
                or c.get("member") is not None
            )
            public[str(cname)] = redact(
                {
                    k: v
                    for k, v in c.items()
                    if k in {"protocol", "ip", "host", "port", "proxy", "console_server_ip", "member", "username"}
                }
            ) | {"is_console": is_console}
            proxy_plan, proxy_error = (None, None)
            if proxy_name:
                proxy_plan, proxy_error = self._resolve_proxy(str(proxy_name), merged)
            plans[str(cname)] = ConnectionPlan(
                name=str(cname),
                protocol=protocol,
                host=host,
                port=port,
                proxy=proxy_plan,
                proxy_error=proxy_error,
                is_console=is_console,
                extra={"console_server_ip": c.get("console_server_ip"), "member": c.get("member")},
            )
            if (
                mgmt_ip is None
                and host
                and host not in LOOPBACKS
                and protocol in {"ssh", "https", "http", "netconf", "rest"}
                and not proxy_name
            ):
                mgmt_ip = host
        if mgmt_ip is None:
            for c in public.values():
                h = c.get("ip") or c.get("host")
                if h and h not in LOOPBACKS and not c.get("proxy") and not c.get("is_console"):
                    mgmt_ip = str(h)
                    break
        return public, plans, mgmt_ip

    # -- device ---------------------------------------------------------------------------------------
    def device(
        self, name: str, data: dict[str, Any], merged: dict[str, Any], origin: Path, root: Path, org_id: str | None
    ) -> DeviceSpec:
        custom = data.get("custom") or {}
        if not isinstance(custom, dict):
            custom = {}
        meraki = custom.get("meraki_device") if isinstance(custom.get("meraki_device"), dict) else {}
        lk = custom.get("labkeeper") if isinstance(custom.get("labkeeper"), dict) else {}
        dtype = data.get("type")
        os_name = data.get("os")
        platform = data.get("platform")
        members = custom.get("members") if isinstance(custom.get("members"), list) else []
        member0 = members[0] if members and isinstance(members[0], dict) else {}
        serial = _first(meraki.get("serial"), data.get("serial"), custom.get("serial"), member0.get("serial"))
        # sku ("MS350-24X") is more specific than model ("MS350") in system_test files
        model = _first(lk.get("model"), meraki.get("model"), custom.get("sku"), data.get("model"), custom.get("model"))
        location = _first(lk.get("location"), meraki.get("location"), custom.get("location"), data.get("location"))
        public_conns, plans, mgmt_ip = self._connections(data, merged)
        mgmt_ip = _first(lk.get("mgmt_ip"), data.get("mgmt_ip"), mgmt_ip)
        # ``chassis_type: stack`` without a serial is a logical stack (its members are separate devices).
        # With a serial it is one physical member of a stack, as in files that list each member on its own.
        is_virtual = bool(lk.get("virtual")) or (str(data.get("chassis_type", "")).lower() == "stack" and not serial)
        stack_name = _first(meraki.get("stack"), custom.get("stack_name"))
        rack, position = parse_location(location, self.location_re)
        if lk.get("rack"):
            rack = str(lk["rack"])
        if lk.get("position") is not None:
            position = _as_int(lk.get("position"))
        is_meraki = (
            str(dtype or "").lower().startswith("meraki")
            or bool(meraki)
            or bool(serial and MERAKI_SERIAL_RE.match(str(serial).strip().upper()))
        )
        # Serial formats and vendor names describe identity; they do not opt a user into a cloud.
        provider = lk.get("provider") or ("reachability" if mgmt_ip else None)
        shared = lk.get("shared")
        if shared is None:
            shared = str(dtype or "").lower() in self.shared_types
        rel_origin = origin.relative_to(root) if origin.is_relative_to(root) else origin
        if serial:
            key = f"serial:{str(serial).strip().upper()}"
        elif is_virtual:
            # Logical stacks are often copied into several base files; the name identifies them.
            key = f"virtual:{name}"
        elif mgmt_ip:
            key = f"addr:{name}@{mgmt_ip}"
        else:
            key = f"file:{rel_origin.as_posix()}#{name}"
        tags = lk.get("tags") or custom.get("tags") or []
        return DeviceSpec(
            key=key,
            name=str(name),
            origin=origin,
            type=str(dtype) if dtype else None,
            os=str(os_name) if os_name else None,
            platform=str(platform) if platform else None,
            alias=str(data.get("alias")) if data.get("alias") and data.get("alias") != name else None,
            vendor=("Cisco Meraki" if is_meraki and not (custom.get("vendor") or lk.get("vendor"))
                    else _vendor_for(dtype, os_name, {**custom, **lk})),
            model=str(model) if model else None,
            serial=str(serial).strip() if serial else None,
            role=_role_for(dtype, platform, model, lk.get("role")),
            mgmt_ip=str(mgmt_ip) if mgmt_ip else None,
            is_shared=bool(shared),
            is_virtual=is_virtual,
            stack_name=str(stack_name) if stack_name else None,
            org_id=org_id,
            provider=provider,
            location=str(location) if location else None,
            rack=None if is_virtual else rack,
            rack_position=None if is_virtual else position,
            u_height=_as_int(lk.get("u_height")) or (0 if is_virtual else 1),
            network=_first(lk.get("network"), custom.get("network")),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            connections=public_conns,
            custom=redact(custom),
            plans=plans,
        )


def _org_id(merged: dict[str, Any]) -> str | None:
    custom = (merged.get("testbed") or {}).get("custom") or {}
    if not isinstance(custom, dict):
        return None
    dash = custom.get("dashboard_config") or {}
    org = (
        (dash.get("org_id") if isinstance(dash, dict) else None)
        or custom.get("org_id")
        or custom.get("organization_id")
    )
    return str(org) if org not in (None, "") else None


def _topology(merged: dict[str, Any]) -> dict[str, Any]:
    topo = merged.get("topology") or {}
    out: dict[str, Any] = {}
    if not isinstance(topo, dict):
        return out
    for dev, spec in topo.items():
        ifaces = (spec or {}).get("interfaces") if isinstance(spec, dict) else None
        if not isinstance(ifaces, dict):
            continue
        out[str(dev)] = {
            str(name): {k: v for k, v in (i or {}).items() if k in {"tag", "type", "link", "alias", "media", "ipv4", "ipv6", "description"}}
            for name, i in ifaces.items()
            if isinstance(i, dict) or i is None
        }
    return out


def _testbed_label(path: Path) -> str:
    """Cheap name lookup for allowlisting: testbed.name from the file itself, else the file stem."""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
        name = (raw.get("testbed") or {}).get("name") if isinstance(raw, dict) else None
    except Exception:
        name = None
    return str(name or path.stem)


def _included(path: Path, root: Path, patterns: list[str]) -> bool:
    if not patterns:
        return True
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
    candidates = {_testbed_label(path).lower(), path.stem.lower(), rel.lower()}
    return any(fnmatch.fnmatch(c, pat.lower()) for pat in patterns for c in candidates)


def scan(
    roots: list[Path], normalizer: Normalizer, exclude: list[str] | None = None, include: list[str] | None = None
) -> ScanResult:
    roots = [r.resolve() for r in roots if r.exists()]
    loader = TestbedLoader(roots)
    files: list[tuple[Path, Path]] = []
    for root in roots:
        candidates = [root] if root.is_file() else sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")])
        for p in candidates:
            rel = p.relative_to(root).as_posix() if root.is_dir() else p.name
            if any(fnmatch.fnmatch(rel, pat) for pat in exclude or []):
                continue
            if any(part.startswith(".") for part in p.relative_to(root).parts) if root.is_dir() else False:
                continue
            files.append((p.resolve(), root.parent if root.is_file() else root))

    extended: set[Path] = set()
    for path, _root in files:
        extended.update(loader.raw_extends(path))

    devices: dict[str, DeviceSpec] = {}
    testbeds: list[TestbedSpec] = []
    reports: list[FileReport] = []
    # Testbeds first, so that with an allowlist a base file is only read when a tracked testbed uses it.
    for path, root in sorted(files, key=lambda f: f[0] in extended):
        report = FileReport(path=path, root=root, kind="base" if path in extended else "testbed")
        if report.kind == "testbed" and not _included(path, root, include or []):
            report.kind = "excluded"
            reports.append(report)
            continue
        if report.kind == "base" and include and path not in loader._cache:
            reports.append(report)  # only used by testbeds that are not tracked
            continue
        try:
            report.mtime = path.stat().st_mtime
            loaded = loader.load(path)
        except TestbedFileError as exc:
            report.kind = "invalid"
            report.error = str(exc)
            reports.append(report)
            continue
        merged = loaded.data
        devs = merged.get("devices") or {}
        if not isinstance(devs, dict):
            report.kind = "invalid"
            report.error = "'devices' must be a mapping"
            reports.append(report)
            continue
        report.device_count = len(devs)
        if report.kind == "base":
            reports.append(report)
            continue
        if not devs:
            report.kind = "invalid"
            report.error = "no devices (nothing to book)"
            reports.append(report)
            continue
        org_id = _org_id(merged)
        keys: list[str] = []
        for name, ddata in devs.items():
            if not isinstance(ddata, dict):
                continue
            origin = loaded.origins.get(str(name), path)
            spec = normalizer.device(str(name), ddata, merged, origin, root, org_id)
            existing = devices.get(spec.key)
            if existing is None:
                devices[spec.key] = spec
            else:
                # First definition wins, but fill gaps (e.g. org id only known in some testbeds).
                existing.org_id = existing.org_id or spec.org_id
                for cname, plan in spec.plans.items():
                    current = existing.plans.get(cname)
                    if current is None or (current.proxy is None and plan.proxy is not None):
                        existing.plans[cname] = plan
            keys.append(spec.key)
        tb_meta = merged.get("testbed") or {}
        rel = path.relative_to(root)
        tb_custom = tb_meta.get("custom") if isinstance(tb_meta.get("custom"), dict) else {}
        lk = tb_custom.get("labkeeper") if isinstance(tb_custom.get("labkeeper"), dict) else {}
        spec = TestbedSpec(
            key=rel.as_posix(),
            name=str(tb_meta.get("name") or path.stem),
            path=path,
            root=root,
            group=rel.parent.as_posix() if rel.parent.as_posix() != "." else None,
            org_id=org_id,
            custom=redact(tb_custom),
            topology=_topology(merged),
            device_keys=list(dict.fromkeys(keys)),
            description=lk.get("description") or tb_custom.get("description"),
            tags=[str(t) for t in (lk.get("tags") or [])],
        )
        report.testbed_key = spec.key
        testbeds.append(spec)
        reports.append(report)
    return ScanResult(devices=devices, testbeds=testbeds, files=reports, roots=roots)


MASK_LINE_RE = re.compile(
    r"^(\s*-?\s*(?:[\w-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|community)[\w-]*|enable)\s*:\s*)(\S.*)$",
    re.IGNORECASE,
)


def masked_source(text: str) -> str:
    """YAML text with secret values masked, for display."""
    out = []
    for line in text.splitlines():
        m = MASK_LINE_RE.match(line)
        if m and not m.group(2).strip().startswith(("{", "[")) and m.group(2).strip() not in ("", "|", ">"):
            out.append(m.group(1) + "••••••")
        else:
            out.append(line)
    return "\n".join(out)
