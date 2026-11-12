"""The data of the mock systems: loaded from the committed seed, kept in memory, saved to a file."""

import json
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fleetdeck_common.errors import ConflictError, NotFoundError

Record = dict[str, Any]

#: The collections of each system. One JSON file per system in the seed directory.
COLLECTIONS: dict[str, tuple[str, ...]] = {
    "patient-record": ("patients", "encounters"),
    "billing": ("claims", "payers"),
    "erp": ("revenue_entries",),
    "docket": ("legal_cases", "deadlines"),
    "contracts": ("contracts",),
}


class Store:
    """All records of all systems, plus the drafts agents have prepared."""

    def __init__(self, seed_dir: Path, state_file: str = "", today: str = "") -> None:
        self._seed_dir = seed_dir
        self._state_file = Path(state_file) if state_file else None
        self._today = today
        self._lock = threading.Lock()
        self.data: dict[str, list[Record]] = {}
        self.drafts: dict[str, Record] = {}
        if self._state_file and self._state_file.is_file():
            saved = json.loads(self._state_file.read_text())
            self.data, self.drafts = saved["data"], saved["drafts"]
        else:
            self.reset()

    def today(self) -> date:
        """Return the demo's current date."""
        return date.fromisoformat(self._today) if self._today else datetime.now(UTC).date()

    def reset(self) -> None:
        """Throw away all changes and reload the seed."""
        with self._lock:
            self.data, self.drafts = {}, {}
            for system, collections in COLLECTIONS.items():
                file = self._seed_dir / f"{system}.json"
                loaded = json.loads(file.read_text()) if file.is_file() else {}
                for name in collections:
                    self.data[name] = list(loaded.get(name, []))
        self.save()

    def save(self) -> None:
        """Write the current state to the state file, if one is configured."""
        if self._state_file is None:
            return
        with self._lock:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps({"data": self.data, "drafts": self.drafts}))
            temporary.replace(self._state_file)

    def all(self, collection: str) -> list[Record]:
        """Return every record of a collection."""
        return self.data[collection]

    def get(self, collection: str, record_id: str) -> Record:
        """Return one record by id or raise :class:`NotFoundError`."""
        for record in self.data[collection]:
            if record["id"] == record_id:
                return record
        raise NotFoundError(f"{collection}: {record_id} not found")

    def add(self, collection: str, record: Record) -> Record:
        """Append a record and save."""
        self.data[collection].append(record)
        self.save()
        return record

    def new_draft(
        self,
        system: str,
        kind: str,
        *,
        author: str,
        case_id: str | None,
        title: str,
        payload: Record,
    ) -> Record:
        """Store something an agent prepared. It changes nothing until a person confirms it."""
        draft: Record = {
            "id": f"DR-{uuid.uuid4().hex[:10].upper()}",
            "system": system,
            "kind": kind,
            "author": author,
            "case_id": case_id,
            "title": title,
            "payload": payload,
            "status": "draft",
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.drafts[draft["id"]] = draft
        self.save()
        return draft

    def take_draft(self, system: str, draft_id: str) -> Record:
        """Return a pending draft of the given system or raise."""
        draft = self.drafts.get(draft_id)
        if draft is None or draft["system"] != system:
            raise NotFoundError(f"{system}: draft {draft_id} not found")
        if draft["status"] != "draft":
            raise ConflictError(f"draft {draft_id} is already {draft['status']}")
        return draft
