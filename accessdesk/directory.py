"""The company directory: people, systems, and the access each person already holds.

Loaded from a YAML file. Everything in the shipped directory is invented (the company is "Northwind", a fictional
mid-size software firm). The directory is also where current grants are looked up, so the gate knows what the
requester already has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .types import AccessRequest, Level, RequestContext


@dataclass
class Person:
    id: str
    name: str
    role: str
    team: str
    manager: str = ""
    employment: str = "employee"  # employee | contractor | intern


@dataclass
class System:
    id: str
    name: str
    owner: str
    sensitivity: str = "medium"
    description: str = ""
    # Default access level each role is entitled to without review. Role name -> level.
    role_defaults: dict[str, str] = field(default_factory=dict)


class Directory:
    def __init__(self, people: dict[str, Person], systems: dict[str, System], current: dict[str, dict[str, str]] | None = None) -> None:
        self.people = people
        self.systems = systems
        # current[person_id][system_id] = level the person already holds (standing access)
        self.current = current or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Directory:
        people = {
            p["id"]: Person(
                id=p["id"], name=p["name"], role=p["role"], team=p["team"], manager=p.get("manager", ""), employment=p.get("employment", "employee")
            )
            for p in data.get("people", [])
        }
        systems = {
            s["id"]: System(
                id=s["id"],
                name=s["name"],
                owner=s["owner"],
                sensitivity=s.get("sensitivity", "medium"),
                description=s.get("description", ""),
                role_defaults=dict(s.get("role_defaults", {})),
            )
            for s in data.get("systems", [])
        }
        current = {pid: dict(levels) for pid, levels in (data.get("current_access") or {}).items()}
        return cls(people, systems, current)

    @classmethod
    def load(cls, path: str | Path) -> Directory:
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def current_level(self, person_id: str, system_id: str) -> Level:
        level = self.current.get(person_id, {}).get(system_id, "none")
        # a role default also counts as standing access
        system = self.systems.get(system_id)
        person = self.people.get(person_id)
        if system and person:
            default = system.role_defaults.get(person.role, "none")
            from .types import LEVEL_RANK

            if LEVEL_RANK.get(default, 0) > LEVEL_RANK.get(level, 0):
                level = default
        return level  # type: ignore[return-value]

    def enrich(self, request: AccessRequest) -> RequestContext:
        person = self.people.get(request.requester)
        system = self.systems.get(request.system)
        return RequestContext(
            request=request,
            requester_role=person.role if person else "unknown",
            requester_team=person.team if person else "unknown",
            employment=person.employment if person else "employee",
            manager=person.manager if person else "",
            system_owner=system.owner if system else "",
            system_sensitivity=system.sensitivity if system else "high",
            current_level=self.current_level(request.requester, request.system),
            system_description=system.description if system else "",
        )
