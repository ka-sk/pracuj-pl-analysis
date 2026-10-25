from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class IdName(BaseModel):
    id: int
    name: str


class CodeName(BaseModel):
    code: str
    name: str


class CategoryDim(BaseModel):
    """A category. Top-level categories are rows too, with parent_id = None."""
    id: int
    name: str
    parent_id: Optional[int] = None


class LocationDim(BaseModel):
    """One place, identified by the full (country, region, location) path.

    The location id alone is not trusted to be unique, so the dict key is
    `location_key` = "<country_id>-<region_id>-<location_id>" (same shape as
    the offer's partitionKey). Region/location may be empty, e.g. when abroad.
    """
    location_key: str
    is_abroad: bool = False
    country_id: Optional[int] = None
    country: Optional[str] = None
    region_id: Optional[int] = None
    region: Optional[str] = None
    location_id: Optional[int] = None
    location: Optional[str] = None


class TimeUnitDim(BaseModel):
    id: int
    name: str                         # long form, e.g. "miesięcznie"
    short_name: Optional[str] = None  # e.g. "mies."


class Dimensions(BaseModel):
    """Lookup rows seen in ONE offer, keyed by id (or code / location key).

    The `add_*` methods take a raw JSON fragment, register its row and return
    the key the offer should store (None if the fragment is empty/incomplete).
    The storage layer upserts the rows right after each offer is parsed.
    """
    categories: dict[int, CategoryDim] = Field(default_factory=dict)
    locations: dict[str, LocationDim] = Field(default_factory=dict)
    position_levels: dict[int, IdName] = Field(default_factory=dict)
    work_schedules: dict[int, IdName] = Field(default_factory=dict)
    contract_types: dict[int, IdName] = Field(default_factory=dict)
    work_modes: dict[str, CodeName] = Field(default_factory=dict)
    salary_kinds: dict[str, CodeName] = Field(default_factory=dict)
    time_units: dict[int, TimeUnitDim] = Field(default_factory=dict)

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _id_name(table: dict, item: Optional[dict]) -> Optional[int]:
        if not item or item.get("id") is None or not item.get("name"):
            return None
        table[item["id"]] = IdName(id=item["id"], name=item["name"])
        return item["id"]

    @staticmethod
    def _code_name(table: dict, item: Optional[dict]) -> Optional[str]:
        if not item or not item.get("code") or not item.get("name"):
            return None
        table[item["code"]] = CodeName(code=item["code"], name=item["name"])
        return item["code"]

    # ---------------------------------------------------------- add_* API
    def add_category(self, c: Optional[dict]) -> Optional[int]:
        """Registers the category and, recursively, its parent (parent first)."""
        if not c or c.get("id") is None:
            return None
        parent_id = self.add_category(c.get("parent"))
        self.categories[c["id"]] = CategoryDim(
            id=c["id"], name=c.get("name", ""), parent_id=parent_id)
        return c["id"]

    def add_location(self, w: Optional[dict]) -> Optional[str]:
        """Takes one raw workplace; returns its "<country>-<region>-<location>" key."""
        country = (w or {}).get("country") or {}
        if country.get("id") is None:
            return None
        region = w.get("region") or {}
        location = (w.get("inlandLocation") or {}).get("location") or {}
        key = "-".join("" if x is None else str(x)
                       for x in (country["id"], region.get("id"), location.get("id")))
        self.locations[key] = LocationDim(
            location_key=key,
            is_abroad=bool(w.get("isAbroad")),
            country_id=country["id"], country=country.get("name"),
            region_id=region.get("id"), region=region.get("name"),
            location_id=location.get("id"), location=location.get("name"),
        )
        return key

    def add_position_level(self, item: Optional[dict]) -> Optional[int]:
        return self._id_name(self.position_levels, item)

    def add_work_schedule(self, item: Optional[dict]) -> Optional[int]:
        return self._id_name(self.work_schedules, item)

    def add_contract_type(self, item: Optional[dict]) -> Optional[int]:
        return self._id_name(self.contract_types, item)

    def add_work_mode(self, item: Optional[dict]) -> Optional[str]:
        return self._code_name(self.work_modes, item)

    def add_salary_kind(self, item: Optional[dict]) -> Optional[str]:
        return self._code_name(self.salary_kinds, item)

    def add_time_unit(self, u: Optional[dict]) -> Optional[int]:
        if not u or u.get("id") is None:
            return None
        long_name = (u.get("longForm") or {}).get("name")
        short_name = (u.get("shortForm") or {}).get("name")
        if not (long_name or short_name):
            return None
        self.time_units[u["id"]] = TimeUnitDim(
            id=u["id"], name=long_name or short_name, short_name=short_name)
        return u["id"]