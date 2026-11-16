"""Reverse dependency propagation including cycles and the changed module itself."""


def affected_modules(
    changed: frozenset[str], dependencies: dict[str, tuple[str, ...]]
) -> frozenset[str]:
    affected = set(changed)
    pending = list(changed)
    reverse: dict[str, set[str]] = {}
    for consumer, providers in dependencies.items():
        for provider in providers:
            reverse.setdefault(provider, set()).add(consumer)
    while pending:
        for consumer in reverse.get(pending.pop(), set()):
            if consumer not in affected:
                affected.add(consumer)
                pending.append(consumer)
    return frozenset(affected)
