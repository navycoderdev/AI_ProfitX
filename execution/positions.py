from threading import RLock

from execution.types import PositionOwner


class PositionManager:
    """Single-writer position ownership. A module must acquire ownership before it can manage an exit."""
    def __init__(self) -> None:
        self._owners: dict[int, PositionOwner] = {}; self._leases: dict[int, str] = {}; self._lock = RLock()

    def register(self, position_id: int, owner: PositionOwner) -> None:
        with self._lock:
            existing = self._owners.get(position_id)
            if existing and existing != owner: raise ValueError("Position is already owned by a different trade.")
            self._owners[position_id] = owner

    def acquire(self, position_id: int, module_id: str) -> None:
        with self._lock:
            lease = self._leases.get(position_id)
            if lease and lease != module_id: raise ValueError(f"Position is currently managed by {lease}.")
            self._leases[position_id] = module_id

    def release(self, position_id: int, module_id: str) -> None:
        with self._lock:
            if self._leases.get(position_id) == module_id: self._leases.pop(position_id)

    def owner(self, position_id: int) -> PositionOwner | None: return self._owners.get(position_id)
    def close(self, position_id: int) -> None:
        with self._lock: self._owners.pop(position_id, None); self._leases.pop(position_id, None)
