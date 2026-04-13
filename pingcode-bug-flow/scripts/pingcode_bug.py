#!/usr/bin/env python3
"""PingCode work-item helper.

Capabilities:
- Read work items by identifier
- List work items and filter by belong/project/status
- Update work-item status
- Maintain a local pending-verification ledger for "AI fixed but not verified"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_BASE_URL = "https://open.pingcode.com"
DEFAULT_TIMEOUT = 25
DEFAULT_TRACKER_FILE = os.path.expanduser(
    "~/.codex/skills/pingcode-bug-flow/state/pending_verify.json"
)


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_base_url(url: str) -> str:
    return url.rstrip("/")


def _normalize(s: Any) -> str:
    return str(s or "").strip().lower()


def _normalize_compact(s: Any) -> str:
    return _normalize(s).replace(" ", "")


def _build_url(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    query = ""
    if params:
        compact = {k: v for k, v in params.items() if v is not None and v != ""}
        query = urllib.parse.urlencode(compact)
    return f"{base_url}{path}" + (f"?{query}" if query else "")


def _request_json(
    method: str,
    base_url: str,
    path: str,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    _retry_alt_prefix: bool = True,
) -> Dict[str, Any]:
    url = _build_url(base_url, path, params)
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            text = resp.read().decode("utf-8")
            if not text.strip():
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                # PingCode environments sometimes expose different API prefixes.
                if _retry_alt_prefix and path.startswith("/open/v1/"):
                    alt_path = path.replace("/open/v1/", "/v1/", 1)
                    return _request_json(
                        method,
                        base_url,
                        alt_path,
                        token,
                        params=params,
                        body=body,
                        _retry_alt_prefix=False,
                    )
                if _retry_alt_prefix and path.startswith("/v1/"):
                    alt_path = path.replace("/v1/", "/open/v1/", 1)
                    return _request_json(
                        method,
                        base_url,
                        alt_path,
                        token,
                        params=params,
                        body=body,
                        _retry_alt_prefix=False,
                    )
                preview = text[:300].replace("\n", "\\n")
                raise RuntimeError(
                    f"Non-JSON response {method.upper()} {path} "
                    f"(Content-Type: {content_type or 'unknown'}): {preview}"
                ) from exc
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if (
            _retry_alt_prefix
            and method.upper() == "GET"
            and exc.code in (400, 404)
            and path.startswith("/open/v1/")
        ):
            alt_path = path.replace("/open/v1/", "/v1/", 1)
            return _request_json(
                method,
                base_url,
                alt_path,
                token,
                params=params,
                body=body,
                _retry_alt_prefix=False,
            )
        if (
            _retry_alt_prefix
            and method.upper() == "GET"
            and exc.code in (400, 404)
            and path.startswith("/v1/")
        ):
            alt_path = path.replace("/v1/", "/open/v1/", 1)
            return _request_json(
                method,
                base_url,
                alt_path,
                token,
                params=params,
                body=body,
                _retry_alt_prefix=False,
            )
        raise RuntimeError(f"HTTP {exc.code} {method.upper()} {path}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error {method.upper()} {path}: {exc}") from exc


def _pick_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []

    candidate_keys = ("value", "values", "items", "list", "data", "records", "rows")
    for key in candidate_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    nested_keys = ("data", "result", "response")
    for key in nested_keys:
        nested = data.get(key)
        if isinstance(nested, dict):
            lst = _pick_list(nested)
            if lst:
                return lst
    return []


def _pick_item(resp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(resp, dict):
        return {}
    if isinstance(resp.get("value"), dict):
        return resp["value"]
    if isinstance(resp.get("data"), dict):
        return resp["data"]
    if isinstance(resp.get("result"), dict):
        return resp["result"]
    return resp


def _extract_identifier_candidates(item: Dict[str, Any]) -> Iterable[str]:
    keys = ("identifier", "code", "serial_number", "work_item_code", "name")
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            yield v.strip()


def _extract_title(item: Dict[str, Any]) -> str:
    for k in ("title", "name", "summary"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _extract_status_name(item: Dict[str, Any]) -> str:
    for k in ("state_name", "status_name", "state", "status"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            name = v.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def _extract_project_id(item: Dict[str, Any]) -> Optional[Any]:
    if item.get("project_id") is not None:
        return item.get("project_id")
    project = item.get("project")
    if isinstance(project, dict):
        return project.get("id")
    return None


def _extract_type_id(item: Dict[str, Any]) -> Optional[Any]:
    if item.get("work_item_type_id") is not None:
        return item.get("work_item_type_id")
    typ = item.get("work_item_type")
    if isinstance(typ, dict):
        return typ.get("id")
    return None


def _extract_belong(item: Dict[str, Any]) -> str:
    candidates = [
        item.get("belong"),
        item.get("belong_name"),
        item.get("module_name"),
        item.get("space_name"),
        item.get("project_name"),
    ]

    project = item.get("project")
    if isinstance(project, dict):
        candidates.extend([project.get("name"), project.get("title")])

    module = item.get("module")
    if isinstance(module, dict):
        candidates.extend([module.get("name"), module.get("title")])

    space = item.get("space")
    if isinstance(space, dict):
        candidates.extend([space.get("name"), space.get("title")])

    for v in candidates:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _fetch_detail(base_url: str, token: str, wid: Any) -> Dict[str, Any]:
    detail_resp = _request_json("GET", base_url, f"/open/v1/project/work_items/{wid}", token)
    return _pick_item(detail_resp)


def _find_work_item(
    base_url: str,
    token: str,
    identifier: str,
    max_pages: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    target = _normalize(identifier)
    last_resp: Dict[str, Any] = {}

    direct_filters = [
        {"identifier": identifier},
        {"q": identifier},
        {"query": identifier},
        {},
    ]

    for extra in direct_filters:
        for page_index in range(1, max_pages + 1):
            params = {"page_index": page_index, "page_size": 50}
            params.update(extra)
            resp = _request_json("GET", base_url, "/open/v1/project/work_items", token, params=params)
            last_resp = resp
            items = _pick_list(resp)
            if not items and page_index == 1:
                break
            for item in items:
                candidates = list(_extract_identifier_candidates(item))
                if any(_normalize(x) == target for x in candidates):
                    return item, resp
            if len(items) < 50:
                break

    raise RuntimeError(
        f"Work item '{identifier}' not found. Last response keys: {list(last_resp.keys()) if isinstance(last_resp, dict) else type(last_resp)}"
    )


def _resolve_state_id(
    base_url: str,
    token: str,
    project_id: Any,
    work_item_type_id: Any,
    target_status: str,
) -> str:
    # Newer PingCode OpenAPI exposes project-level states here.
    resp = _request_json(
        "GET",
        base_url,
        "/v1/project/work_item_states",
        token,
        params={"project_id": project_id},
    )
    states = _pick_list(resp)

    # Backward compatibility for older/alternative endpoints.
    if not states:
        resp = _request_json(
            "GET",
            base_url,
            "/open/v1/project/work_item/work_item_states",
            token,
            params={"project_id": project_id, "work_item_type_id": work_item_type_id},
        )
        states = _pick_list(resp)
    if not states:
        raise RuntimeError("No states found from work_item_states API")

    t1 = _normalize(target_status)
    t2 = _normalize_compact(target_status)

    for s in states:
        name = str(s.get("name", "")).strip()
        if _normalize(name) == t1 or _normalize_compact(name) == t2:
            sid = s.get("id")
            if sid is not None:
                return str(sid)

    all_names = ", ".join(sorted(str(s.get("name", "")).strip() for s in states if s.get("name")))
    raise RuntimeError(f"Status '{target_status}' not found. Available statuses: {all_names}")


def _set_status_by_name(
    base_url: str,
    token: str,
    item: Dict[str, Any],
    identifier: str,
    target_status: str,
) -> Dict[str, Any]:
    wid = item.get("id")
    if wid is None:
        raise RuntimeError("Matched work item has no id")

    project_id = _extract_project_id(item)
    work_item_type_id = _extract_type_id(item)
    if project_id is None:
        detail = _fetch_detail(base_url, token, wid)
        if detail:
            item = detail
        project_id = _extract_project_id(item)
        if work_item_type_id is None:
            work_item_type_id = _extract_type_id(item)

    if project_id is None:
        raise RuntimeError("Cannot infer project_id")

    state_id = _resolve_state_id(
        base_url,
        token,
        project_id=project_id,
        work_item_type_id=work_item_type_id,
        target_status=target_status,
    )

    patch_resp = _request_json(
        "PATCH",
        base_url,
        f"/v1/project/work_items/{wid}",
        token,
        body={"state_id": state_id},
    )

    return {
        "identifier": identifier,
        "work_item_id": wid,
        "target_status": target_status,
        "target_state_id": state_id,
        "patch_response": patch_resp,
    }


def _add_comment(
    base_url: str,
    token: str,
    work_item_id: Any,
    content: str,
) -> Dict[str, Any]:
    if work_item_id is None:
        raise RuntimeError("Cannot add comment: work_item_id is missing")
    return _request_json(
        "POST",
        base_url,
        f"/v1/project/work_items/{work_item_id}/comments",
        token,
        body={"content": content},
    )


def _must_token(args: argparse.Namespace) -> str:
    token = args.token or os.getenv("PINGCODE_TOKEN")
    if not token:
        raise RuntimeError("Missing token: pass --token or set PINGCODE_TOKEN")
    return token


def _read_tracker(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "items": []}
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data
    except json.JSONDecodeError:
        return {"version": 1, "items": []}


def _write_tracker(path: str, data: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _tracker_path(args: argparse.Namespace) -> str:
    if args.tracker_file:
        return args.tracker_file
    return os.getenv("PINGCODE_TRACKER_FILE", DEFAULT_TRACKER_FILE)


def cmd_extract(args: argparse.Namespace) -> int:
    source = args.text
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            source = f.read()
    if not source:
        _stderr("No text input. Use --text or --from-file")
        return 2
    m = re.search(r"\b[A-Z]{2,}-\d+\b", source)
    if not m:
        _stderr("No work-item identifier like YDZ-279 found")
        return 1
    print(m.group(0))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    token = _must_token(args)
    base = _clean_base_url(args.base_url)

    item, _ = _find_work_item(base, token, args.identifier, args.max_pages)
    wid = item.get("id")
    if wid is not None:
        try:
            detail = _fetch_detail(base, token, wid)
            if detail:
                item = detail
        except Exception as exc:  # noqa: BLE001
            _stderr(f"Warning: detail fetch failed for id={wid}: {exc}")

    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    token = _must_token(args)
    base = _clean_base_url(args.base_url)

    item, _ = _find_work_item(base, token, args.identifier, args.max_pages)
    result = _set_status_by_name(base, token, item, args.identifier, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    token = _must_token(args)
    base = _clean_base_url(args.base_url)

    all_items: List[Dict[str, Any]] = []
    for page_index in range(1, args.max_pages + 1):
        params: Dict[str, Any] = {"page_index": page_index, "page_size": args.page_size}
        if args.query:
            params["q"] = args.query

        resp = _request_json("GET", base, "/open/v1/project/work_items", token, params=params)
        items = _pick_list(resp)
        if not items:
            break
        all_items.extend(items)
        if len(items) < args.page_size:
            break

    belong_kw = _normalize(args.belong)
    status_kw = _normalize(args.status)

    out: List[Dict[str, Any]] = []
    for item in all_items:
        identifier = next(iter(_extract_identifier_candidates(item)), "")
        title = _extract_title(item)
        status_name = _extract_status_name(item)
        belong = _extract_belong(item)

        if belong_kw and belong_kw not in _normalize(belong):
            continue
        if status_kw and status_kw != _normalize(status_name):
            continue

        out.append(
            {
                "identifier": identifier,
                "id": item.get("id"),
                "title": title,
                "status": status_name,
                "belong": belong,
            }
        )

    print(json.dumps({"count": len(out), "items": out}, ensure_ascii=False, indent=2))
    return 0


def cmd_mark_pending(args: argparse.Namespace) -> int:
    token = _must_token(args)
    base = _clean_base_url(args.base_url)

    item, _ = _find_work_item(base, token, args.identifier, args.max_pages)
    wid = item.get("id")
    if wid is not None:
        try:
            detail = _fetch_detail(base, token, wid)
            if detail:
                item = detail
        except Exception as exc:  # noqa: BLE001
            _stderr(f"Warning: detail fetch failed for id={wid}: {exc}")

    remote_update = None
    if args.sync_status:
        try:
            remote_update = _set_status_by_name(base, token, item, args.identifier, args.sync_status)
        except Exception as exc:  # noqa: BLE001
            if args.strict_sync:
                raise
            _stderr(f"Warning: sync status failed: {exc}")

    comment_resp = None
    if not args.skip_comment:
        comment_text = args.comment_text.strip()
        if args.note.strip():
            comment_text = f"{comment_text}\n{args.note.strip()}" if comment_text else args.note.strip()
        if comment_text:
            try:
                comment_resp = _add_comment(base, token, item.get("id"), comment_text)
            except Exception as exc:  # noqa: BLE001
                if args.strict_comment:
                    raise
                _stderr(f"Warning: add comment failed: {exc}")

    record = {
        "identifier": args.identifier,
        "work_item_id": item.get("id"),
        "title": _extract_title(item),
        "belong": _extract_belong(item),
        "pingcode_status": _extract_status_name(item),
        "verify_status": "pending",
        "ai_fixed_at": _now(),
        "ai_note": args.note,
        "branch": args.branch,
        "commit": args.commit,
        "sync_status": args.sync_status or "",
        "remote_update": remote_update,
        "comment_text": "" if args.skip_comment else args.comment_text,
        "comment_response": comment_resp,
    }

    tracker_file = ""
    if not args.skip_local_track:
        tracker = _read_tracker(_tracker_path(args))
        records: List[Dict[str, Any]] = [r for r in tracker.get("items", []) if isinstance(r, dict)]
        key = _normalize(args.identifier)
        remain = [r for r in records if _normalize(r.get("identifier")) != key]
        remain.insert(0, record)
        tracker["items"] = remain
        tracker_file = _tracker_path(args)
        _write_tracker(tracker_file, tracker)

    print(
        json.dumps(
            {
                "tracker_file": tracker_file,
                "record": record,
                "message": "Marked as AI-fixed pending verification",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_pending_list(args: argparse.Namespace) -> int:
    tracker = _read_tracker(_tracker_path(args))
    items: List[Dict[str, Any]] = [r for r in tracker.get("items", []) if isinstance(r, dict)]

    out: List[Dict[str, Any]] = []
    belong_kw = _normalize(args.belong)

    for r in items:
        if _normalize(r.get("verify_status")) != "pending":
            continue
        if belong_kw and belong_kw not in _normalize(r.get("belong")):
            continue
        out.append(r)

    print(json.dumps({"count": len(out), "items": out}, ensure_ascii=False, indent=2))
    return 0


def cmd_mark_verified(args: argparse.Namespace) -> int:
    tracker = _read_tracker(_tracker_path(args))
    items: List[Dict[str, Any]] = [r for r in tracker.get("items", []) if isinstance(r, dict)]

    key = _normalize(args.identifier)
    hit = None
    for r in items:
        if _normalize(r.get("identifier")) == key:
            hit = r
            break

    if hit is None:
        raise RuntimeError(f"Identifier not found in tracker: {args.identifier}")

    hit["verify_status"] = "verified" if args.passed else "rejected"
    hit["verified_at"] = _now()
    hit["verify_note"] = args.note

    _write_tracker(_tracker_path(args), tracker)
    print(
        json.dumps(
            {
                "tracker_file": _tracker_path(args),
                "record": hit,
                "message": "Verification status updated",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PingCode bug helper")
    parser.add_argument(
        "--base-url",
        default=os.getenv("PINGCODE_BASE_URL", DEFAULT_BASE_URL),
        help="PingCode OpenAPI base URL, default: https://open.pingcode.com",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="OpenAPI token. If omitted, use env PINGCODE_TOKEN",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Max pages to scan when resolving identifier/list",
    )
    parser.add_argument(
        "--tracker-file",
        default="",
        help=f"Local tracker json path (default: {DEFAULT_TRACKER_FILE})",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_extract = sub.add_parser("extract-identifier", help="Extract YDZ-279 style identifier from text/url")
    p_extract.add_argument("--text", default="", help="Raw text or URL")
    p_extract.add_argument("--from-file", default="", help="Read text from file")
    p_extract.set_defaults(func=cmd_extract)

    p_get = sub.add_parser("get", help="Fetch work item by identifier")
    p_get.add_argument("--identifier", required=True, help="Like YDZ-279")
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set-status", help="Set work item status by identifier")
    p_set.add_argument("--identifier", required=True, help="Like YDZ-279")
    p_set.add_argument("--status", default="已处理", help="Target status name")
    p_set.set_defaults(func=cmd_set_status)

    p_list = sub.add_parser("list", help="List work items with optional filters")
    p_list.add_argument("--query", default="", help="Server-side fuzzy query (if supported)")
    p_list.add_argument("--belong", default="", help="Filter by belong/project keyword")
    p_list.add_argument("--status", default="", help="Filter by exact status name")
    p_list.add_argument("--page-size", type=int, default=50, help="Page size for list")
    p_list.set_defaults(func=cmd_list)

    p_mark_pending = sub.add_parser(
        "mark-pending",
        help="Mark local tracker as AI-fixed pending verification",
    )
    p_mark_pending.add_argument("--identifier", required=True, help="Like YDZ-279")
    p_mark_pending.add_argument("--note", default="", help="AI fix note")
    p_mark_pending.add_argument("--branch", default="", help="Branch name")
    p_mark_pending.add_argument("--commit", default="", help="Commit SHA")
    p_mark_pending.add_argument(
        "--comment-text",
        default="已解决未验证",
        help="Comment text posted to work item thread",
    )
    p_mark_pending.add_argument(
        "--sync-status",
        default="已修改",
        help="Also set pingcode status. Empty string means skip remote status update",
    )
    p_mark_pending.add_argument(
        "--strict-sync",
        action="store_true",
        help="Fail command when sync status update fails",
    )
    p_mark_pending.add_argument(
        "--skip-comment",
        action="store_true",
        help="Skip posting comment to PingCode thread",
    )
    p_mark_pending.add_argument(
        "--strict-comment",
        action="store_true",
        help="Fail command when posting comment fails",
    )
    p_mark_pending.add_argument(
        "--skip-local-track",
        action="store_true",
        help="Do not write local pending tracker file",
    )
    p_mark_pending.set_defaults(func=cmd_mark_pending)

    p_pending_list = sub.add_parser("pending-list", help="List local pending-verification records")
    p_pending_list.add_argument("--belong", default="", help="Filter by belong/project keyword")
    p_pending_list.set_defaults(func=cmd_pending_list)

    p_mark_verified = sub.add_parser("mark-verified", help="Mark local record as verified or rejected")
    p_mark_verified.add_argument("--identifier", required=True, help="Like YDZ-279")
    p_mark_verified.add_argument(
        "--passed",
        action="store_true",
        help="Set verify_status=verified; omit to set rejected",
    )
    p_mark_verified.add_argument("--note", default="", help="Verification note")
    p_mark_verified.set_defaults(func=cmd_mark_verified)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        _stderr(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
