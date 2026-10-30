"""Turn service reports into chat text (commands) and JSON-safe dicts (RPC)."""

from __future__ import annotations

from ..domain import (
    CogPackage,
    CogStatus,
    InstallReport,
    InstallResult,
    LoadOutcome,
    LoadReport,
    UnloadOutcome,
    UnloadReport,
)

_LOAD_MARKS = {
    LoadOutcome.LOADED: "✅",
    LoadOutcome.ALREADY_LOADED: "⏭️",
    LoadOutcome.FAILED: "❌",
    LoadOutcome.NOT_FOUND: "❓",
    LoadOutcome.SKIPPED: "⛔",
}
_UNLOAD_MARKS = {
    UnloadOutcome.UNLOADED: "✅",
    UnloadOutcome.NOT_LOADED: "⏭️",
    UnloadOutcome.SKIPPED: "⛔",
}


# -- chat text ----------------------------------------------------------


def status_table(statuses: list[CogStatus]) -> str:
    """A fixed-width table; the caller boxes and paginates it."""

    if not statuses:
        return "No cogs found."
    rows = [("cog", "source", "loaded", "requirements", "path")]
    for status in statuses:
        package = status.package
        source = _source_label(package) if package else "not found"
        if status.shadowed:
            source += " (shadows " + ", ".join(_source_label(p) for p in status.shadowed) + ")"
        requirements = (
            "missing: " + ", ".join(status.missing_requirements)
            if status.missing_requirements
            else ("ok" if package else "-")
        )
        rows.append(
            (
                status.name,
                source,
                "yes" if status.loaded else "no",
                requirements,
                str(package.directory) if package else "-",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=False))
        + "  "
        + row[-1]
        for row in rows
    )


def install_text(report: InstallReport) -> str:
    lines = [_install_line(report.install)]
    lines.extend(_missing_lines(report.still_missing))
    lines.extend(_unknown_lines(report.unknown_dependencies))
    if report.not_found:
        lines.append("❓ Not found: " + ", ".join(report.not_found))
    if report.install.ran and not report.install.ok and report.install.output:
        lines.append("pip output (tail):\n" + report.install.output)
    return "\n".join(lines)


def load_text(report: LoadReport) -> str:
    if report.error:
        return "❌ " + report.error
    lines: list[str] = []
    if report.order:
        lines.append("Order: " + " → ".join(report.order))
    if report.install is not None:
        lines.append(_install_line(report.install))
    lines.extend(_missing_lines(report.still_missing))
    lines.extend(_unknown_lines(report.unknown_dependencies))
    for result in report.results:
        detail = f": {result.detail}" if result.detail else ""
        lines.append(f"{_LOAD_MARKS[result.outcome]} {result.name} {result.outcome.value}{detail}")
    if report.not_found:
        lines.append("❓ Not found: " + ", ".join(report.not_found))
    if report.install is not None and not report.install.ok and report.install.output:
        lines.append("pip output (tail):\n" + report.install.output)
    return "\n".join(lines) or "Nothing to load."


def unload_text(report: UnloadReport) -> str:
    if report.error:
        return "❌ " + report.error
    lines: list[str] = []
    if report.order:
        lines.append("Order: " + " → ".join(report.order))
    for result in report.results:
        detail = f": {result.detail}" if result.detail else ""
        lines.append(
            f"{_UNLOAD_MARKS[result.outcome]} {result.name} {result.outcome.value}{detail}"
        )
    if report.not_found:
        lines.append("❓ Not found: " + ", ".join(report.not_found))
    return "\n".join(lines) or "Nothing to unload."


def _source_label(package: CogPackage) -> str:
    if package.repo_name:
        return f"{package.source.value}:{package.repo_name}"
    return package.source.value


def _install_line(install: InstallResult) -> str:
    if not install.ran:
        return "📦 Requirements: nothing to install"
    if install.ok:
        return f"📦 Installed {len(install.requirements)} requirement(s)"
    return f"❌ pip install failed (exit code {install.returncode})"


def _missing_lines(still_missing: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        f"⚠️ {name} still missing: {', '.join(missing)}"
        for name, missing in sorted(still_missing.items())
    ]


def _unknown_lines(unknown: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        f"⚠️ {name} requires {', '.join(deps)}, which no cog path provides"
        for name, deps in sorted(unknown.items())
    ]


# -- JSON-safe dicts (RPC) ----------------------------------------------


def package_dict(package: CogPackage) -> dict[str, object]:
    return {
        "name": package.name,
        "source": package.source.value,
        "repo_name": package.repo_name,
        "directory": str(package.directory),
        "root": str(package.root),
        "requirements": list(package.requirements),
        "required_cogs": list(package.required_cogs),
        "hidden": package.hidden,
        "disabled": package.disabled,
    }


def status_dict(statuses: list[CogStatus]) -> dict[str, object]:
    return {
        "cogs": [
            {
                "name": status.name,
                "source": status.package.source.value if status.package else None,
                "package": package_dict(status.package) if status.package else None,
                "loaded": status.loaded,
                "missing_requirements": list(status.missing_requirements),
                "shadowed": [package_dict(package) for package in status.shadowed],
            }
            for status in statuses
        ]
    }


def install_result_dict(install: InstallResult | None) -> dict[str, object] | None:
    if install is None:
        return None
    return {
        "ok": install.ok,
        "ran": install.ran,
        "requirements": list(install.requirements),
        "returncode": install.returncode,
        "output": install.output,
    }


def install_dict(report: InstallReport) -> dict[str, object]:
    return {
        "names": list(report.names),
        "not_found": list(report.not_found),
        "install": install_result_dict(report.install),
        "still_missing": {name: list(m) for name, m in report.still_missing.items()},
        "unknown_dependencies": {
            name: list(deps) for name, deps in report.unknown_dependencies.items()
        },
    }


def load_dict(report: LoadReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "error": report.error,
        "order": list(report.order),
        "results": [
            {"name": r.name, "outcome": r.outcome.value, "detail": r.detail} for r in report.results
        ],
        "not_found": list(report.not_found),
        "install": install_result_dict(report.install),
        "still_missing": {name: list(m) for name, m in report.still_missing.items()},
        "unknown_dependencies": {
            name: list(deps) for name, deps in report.unknown_dependencies.items()
        },
    }


def unload_dict(report: UnloadReport) -> dict[str, object]:
    return {
        "error": report.error,
        "order": list(report.order),
        "results": [
            {"name": r.name, "outcome": r.outcome.value, "detail": r.detail} for r in report.results
        ],
        "not_found": list(report.not_found),
    }
