"""Tagged migration scope and adapter capability selection."""

from pathlib import Path

from .contracts import require

SUITE = "migration/yaml-parity-v1"


def migration_paths(root):
    paths = sorted(p.relative_to(root).as_posix() for p in (Path(root) / SUITE).glob("*.feature"))
    require(bool(paths), "zero_cases", "No migrated features")
    return paths


def selection(case, profile, supported_routes, explicit=False):
    """Capabilities select candidates; missing bindings stay visible as execution gaps."""
    requirements = case.migration
    from .discovery import execution_route

    routes = execution_route(case)["required_routes"]
    capabilities = requirements["sdk_capabilities"]
    missing_routes = sorted(set(routes) - set(supported_routes))
    missing_capabilities = sorted(set(capabilities) - set(profile.get("sdk_capabilities", [])))
    selected = explicit or not missing_capabilities
    if missing_routes:
        reason = "Public candidate operation unavailable: " + ", ".join(missing_routes)
    elif missing_capabilities:
        state = "not declared" if "sdk_capabilities" not in profile else "unavailable"
        reason = f"SDK feature/API capability {state}: " + ", ".join(missing_capabilities)
    else:
        reason = "Public operation and independent SDK feature/API declarations match"
    return {
        "selected": selected,
        "reason": reason,
        "missing_routes": missing_routes,
        "missing_sdk_capabilities": missing_capabilities,
        "explicit": explicit,
    }
