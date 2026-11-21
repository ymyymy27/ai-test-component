"""问题修复台账：从两份审查文档抽取全部条目，校验台账完整性。

用法：
  python scripts/audit_ledger.py --init    # 首次生成台账（已存在则拒绝，--force 覆盖）
  python scripts/audit_ledger.py --check   # 校验台账与文档条目一一对应
  python scripts/audit_ledger.py --require-closed  # 发布门禁：不允许未关闭条目
  python scripts/audit_ledger.py --set PR-001 --status 已验 --commit abc123 --evidence "pytest ..."

台账状态：
  未开始 / 修复中 / 已修复待验 / 已验 / 不修 / 需决策
  - 已修复待验：必须填“修复提交”
  - 已验：必须填“修复提交”和“验证证据”（测试节点、截图路径或真实宿主记录）
  - 不修：必须在“备注”写明理由与决策人
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_05 = REPO_ROOT / "docs/项目文档/一期/05-测试验收/05-全量审查问题清单-2026-11-20.md"
DOC_06 = REPO_ROOT / "docs/项目文档/一期/05-测试验收/06-逐页UI复查-2026-11-21.md"
LEDGER = REPO_ROOT / "docs/项目文档/一期/05-测试验收/07-问题修复台账.md"

STATUSES = ("未开始", "修复中", "已修复待验", "已验", "不修", "需决策")
CLOSED = ("已验", "不修")

_SECTION_05 = re.compile(r"^# ([一二三四五六七八九十]+)、(.+)$")
_ITEM_05_HEAD = re.compile(r"^## ([BFET]-\d+) (.+)$")
_ITEM_05_HEAD2 = re.compile(r"^### (补-[BFET]\d+) (.+)$")
_ITEM_05_BULLET = re.compile(r"^- \*\*(补-F[VSD]\d+) ([^*]+?)\*\*")
_SECTION_06 = re.compile(r"^## ([一二三四五六七八九十]+)、(.+)$")
_ITEM_06 = re.compile(r"^- (PR-\d+) (.+)$")
_LEDGER_ROW = re.compile(
    r"^\| ([^|]+?) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \|$"
)
_ISSUE_ID = re.compile(r"^(?:[BFET]-\d+|补-[BFET]\d+|补-F[VSD]\d+|PR-\d+)$")


@dataclass
class Issue:
    issue_id: str
    source: str
    area: str
    title: str


def _clean_title(text: str) -> str:
    text = text.strip().rstrip("。")
    text = re.split(r"。证据：|证据：", text)[0].strip()
    text = text.replace("|", "\\|")
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def extract_issues() -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()

    def add(issue_id: str, source: str, area: str, title: str) -> None:
        if issue_id in seen:
            raise SystemExit(f"文档条目编号重复：{issue_id}")
        seen.add(issue_id)
        issues.append(Issue(issue_id, source, area, _clean_title(title)))

    area = ""
    for line in DOC_05.read_text(encoding="utf-8").splitlines():
        section = _SECTION_05.match(line)
        if section:
            area = section.group(2).strip()
            continue
        match = _ITEM_05_HEAD.match(line) or _ITEM_05_HEAD2.match(line)
        if match:
            add(match.group(1), "05", area, match.group(2))
            continue
        bullet = _ITEM_05_BULLET.match(line)
        if bullet:
            add(bullet.group(1), "05", area, bullet.group(2))

    area = ""
    for line in DOC_06.read_text(encoding="utf-8").splitlines():
        section = _SECTION_06.match(line)
        if section:
            area = section.group(2).strip()
            continue
        match = _ITEM_06.match(line)
        if match:
            add(match.group(1), "06", area, match.group(2))

    return issues


def rows_from_issues(issues: list[Issue]) -> list[dict[str, str]]:
    return [
        {
            "id": issue.issue_id,
            "source": issue.source,
            "area": issue.area,
            "title": issue.title,
            "status": "未开始",
            "commit": "",
            "evidence": "",
            "note": "",
        }
        for issue in issues
    ]


def render_ledger(rows: list[dict[str, str]]) -> str:
    lines = [
        "# 问题修复台账（2026-11-21）",
        "",
        "本文是[05 全量审查问题清单](05-全量审查问题清单-2026-11-20.md)与"
        "[06 逐页 UI 复查](06-逐页UI复查-2026-11-21.md)的逐条修复台账。",
        "每一行对应一个条目编号；编号、来源、范围、条目由脚本从两份文档抽取，禁止手工增删行。",
        "",
        "## 状态与填写规则",
        "",
        "- `未开始`：默认值。",
        "- `修复中`：已开始但未完成；备注写当前进度或阻塞原因。",
        "- `已修复待验`：必须填“修复提交”（commit hash）。",
        "- `已验`：必须填“修复提交”和“验证证据”"
        "（pytest 节点、面板单测、e2e 用例、截图路径或真实宿主记录）。",
        "- `不修`：必须在“备注”写明理由与决策人，不允许空理由关闭。",
        "- `需决策`：需要产品或范围决定；备注写待决问题，不得当作已处理。",
        "",
        "状态更新使用 `python scripts/audit_ledger.py --set 编号 [编号...] --status 已验 "
        "--commit <hash> --evidence <证据>`，脚本会同步重算汇总。",
        "发布门禁执行 `python scripts/audit_ledger.py --require-closed`：只允许 `已验` 与 `不修`。",
        "",
        "## 汇总",
        "",
        "| 来源 | 条目数 | 未开始 | 修复中 | 已修复待验 | 已验 | 不修 | 需决策 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for source, label in (("05", "05 全量审查"), ("06", "06 逐页复查")):
        subset = [row for row in rows if row["source"] == source]
        cells = [str(sum(1 for row in subset if row["status"] == status)) for status in STATUSES]
        lines.append(f"| {label} | {len(subset)} | " + " | ".join(cells) + " |")
    total_cells = [str(sum(1 for row in rows if row["status"] == status)) for status in STATUSES]
    lines.append(f"| 合计 | {len(rows)} | " + " | ".join(total_cells) + " |")
    lines.extend(
        [
            "",
            "## 台账",
            "",
            "| 编号 | 来源 | 范围 | 条目 | 状态 | 修复提交 | 验证证据 | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['source']} | {row['area']} | {row['title']} | "
            f"{row['status']} | {row['commit']} | {row['evidence']} | {row['note']} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_ledger() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        match = _LEDGER_ROW.match(line)
        if not match:
            continue
        issue_id = match.group(1)
        if not _ISSUE_ID.match(issue_id):
            continue
        rows.append(
            {
                "id": issue_id,
                "source": match.group(2),
                "area": match.group(3),
                "title": match.group(4),
                "status": match.group(5),
                "commit": match.group(6).strip(),
                "evidence": match.group(7).strip(),
                "note": match.group(8).strip(),
            }
        )
    return rows


def update_rows(
    ids: list[str],
    status: str,
    commit: str | None,
    evidence: str | None,
    note: str | None,
) -> None:
    if status not in STATUSES:
        raise SystemExit(f"状态非法：{status}")
    rows = parse_ledger()
    existing = {row["id"] for row in rows}
    missing = [issue_id for issue_id in ids if issue_id not in existing]
    if missing:
        raise SystemExit(f"台账中没有这些条目：{', '.join(missing)}")
    for row in rows:
        if row["id"] not in ids:
            continue
        row["status"] = status
        if commit is not None:
            row["commit"] = commit
        if evidence is not None:
            row["evidence"] = evidence
        if note is not None:
            row["note"] = note
    LEDGER.write_text(render_ledger(rows), encoding="utf-8", newline="\n")


def check(require_closed: bool = False) -> list[str]:
    issues = extract_issues()
    expected = {issue.issue_id: issue for issue in issues}
    rows = parse_ledger()
    errors: list[str] = []

    seen: set[str] = set()
    for row in rows:
        if row["id"] in seen:
            errors.append(f"台账重复行：{row['id']}")
        seen.add(row["id"])
        if row["id"] not in expected:
            errors.append(f"台账多出文档中不存在的条目：{row['id']}")
            continue
        if row["status"] not in STATUSES:
            errors.append(f"{row['id']} 状态非法：{row['status']}")
            continue
        if row["status"] == "已修复待验" and not row["commit"]:
            errors.append(f"{row['id']} 已修复待验但缺少修复提交")
        if row["status"] == "已验":
            if not row["commit"]:
                errors.append(f"{row['id']} 已验但缺少修复提交")
            if not row["evidence"]:
                errors.append(f"{row['id']} 已验但缺少验证证据")
        if row["status"] == "不修" and (
            "理由" not in row["note"] or "决策" not in row["note"]
        ):
            errors.append(f"{row['id']} 不修但备注未写明理由与决策人")
        if require_closed and row["status"] not in CLOSED:
            errors.append(f"{row['id']} 仍未关闭：{row['status']}")

    for issue_id in expected:
        if issue_id not in seen:
            errors.append(f"台账缺少条目：{issue_id}")

    return errors


def print_summary() -> None:
    rows = parse_ledger()
    print(f"台账条目：{len(rows)}")
    for status in STATUSES:
        count = sum(1 for row in rows if row["status"] == status)
        print(f"  {status}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="问题修复台账生成与校验")
    parser.add_argument("--init", action="store_true", help="生成台账（已存在时拒绝）")
    parser.add_argument("--force", action="store_true", help="配合 --init 覆盖已有台账")
    parser.add_argument("--check", action="store_true", help="校验台账与文档一致（默认）")
    parser.add_argument("--require-closed", action="store_true", help="发布门禁：不允许未关闭条目")
    parser.add_argument("--set", nargs="+", metavar="ID", help="更新指定条目的状态")
    parser.add_argument("--status", choices=STATUSES, help="配合 --set 的新状态")
    parser.add_argument("--commit", default=None, help="修复提交")
    parser.add_argument("--evidence", default=None, help="验证证据")
    parser.add_argument("--note", default=None, help="备注")
    args = parser.parse_args()

    if args.set:
        if not args.status:
            print("--set 必须同时给出 --status", file=sys.stderr)
            return 2
        update_rows(args.set, args.status, args.commit, args.evidence, args.note)
        print_summary()
        print("台账已更新")
        return 0

    if args.init:
        if LEDGER.exists() and not args.force:
            print(f"台账已存在，拒绝覆盖：{LEDGER}", file=sys.stderr)
            return 2
        ledger_text = render_ledger(rows_from_issues(extract_issues()))
        LEDGER.write_text(ledger_text, encoding="utf-8", newline="\n")
        print(f"已生成：{LEDGER}")
        print_summary()
        return 0

    if not LEDGER.exists():
        print(f"台账不存在：{LEDGER}，先执行 --init", file=sys.stderr)
        return 2

    errors = check(require_closed=args.require_closed)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"校验失败：{len(errors)} 项", file=sys.stderr)
        return 1
    print_summary()
    print("台账校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
