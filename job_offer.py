from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- helpers

def _names(items: Optional[list[dict]], key: str = "name") -> list[str]:
    """['name' of each dict] from a list like positionLevels / workModes."""
    return [i[key] for i in (items or []) if i.get(key)]


def _find_section(sections: Optional[list[dict]], section_type: str) -> Optional[dict]:
    """Depth-first search through sections and their subSections."""
    for s in sections or []:
        if s.get("sectionType") == section_type:
            return s
        found = _find_section(s.get("subSections"), section_type)
        if found:
            return found
    return None


def _section_items(sections: Optional[list[dict]], section_type: str) -> list[str]:
    """Return the text items of a section, whatever its model type is
    (bullets / multi-paragraph / dictionary with items)."""
    section = _find_section(sections, section_type)
    if not section or not section.get("model"):
        return []
    model = section["model"]
    if "bullets" in model:
        return [b for b in model["bullets"] if b]
    if "paragraphs" in model:
        return [p for p in model["paragraphs"] if p]
    out = []
    for key in ("items", "customItems"):
        for item in model.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                out.append(item["name"])
            elif isinstance(item, str):
                out.append(item)
    return out


def _flags(offer: dict) -> dict[str, bool]:
    """code -> bool for every 'flag' attribute (immediate-employment, shift-work...)."""
    flags: dict[str, bool] = {}
    for attr in (offer.get("primaryAttributes") or []) + (offer.get("secondaryAttributes") or []):
        model = attr.get("model") or {}
        if model.get("modelType") == "flag":
            flags[attr["code"]] = bool(model.get("flag"))
    return flags


# ----------------------------------------------------------------- models

class Contract(BaseModel):
    name: str
    salary_from: Optional[float] = None
    salary_to: Optional[float] = None
    currency: Optional[str] = None
    time_unit: Optional[str] = None
    is_net: Optional[bool] = None


class JobOffer(BaseModel):
    offer_id: int
    employer_id: int
    language: str
    initial_pub_date: datetime
    last_published: datetime
    expiration_date: datetime
    is_active: bool

    job_title: str
    is_archive: bool
    is_withdrawn: bool
    offer_url: str
    employer_name: str

    categories: list[str] = Field(default_factory=list)
    leading_category: Optional[str] = None
    workplaces: list[str] = Field(default_factory=list)
    position_levels: list[str] = Field(default_factory=list)
    entirely_remote_work: bool = False
    work_schedules: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)

    contracts: list[Contract] = Field(default_factory=list)
    salary_lower_limit: Optional[float] = None
    salary_upper_limit: Optional[float] = None

    technologies: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    opt_requirements: list[str] = Field(default_factory=list)
    offered: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)

    many_vacancies: bool = False
    immediate_employment: bool = False
    shift_work: bool = False

    # ------------------------------------------------------------ parsing
    @classmethod
    def from_details_json(cls, raw: dict[str, Any]) -> "JobOffer":
        """Build a JobOffer from the dict returned by get_details()."""
        pub = raw["publicationDetails"]
        attrs = raw["attributes"]
        emp = attrs.get("employment") or {}
        sections = raw.get("sections") or []
        flags = _flags(raw)

        contracts = []
        for c in emp.get("typesOfContracts") or []:
            sal = c.get("salary") or {}
            contracts.append(
                Contract(
                    name=c["name"],
                    salary_from=sal.get("from"),
                    salary_to=sal.get("to"),
                    # nested shapes below are guesses - check against an offer with salary
                    currency=(sal.get("currency") or {}).get("code"),
                    time_unit=(sal.get("timeUnit") or {}).get("name"),
                    is_net=sal.get("isNet"),
                )
            )
        lows = [c.salary_from for c in contracts if c.salary_from is not None]
        highs = [c.salary_to for c in contracts if c.salary_to is not None]

        leading = attrs.get("leadingCategory") or {}

        return cls(
            offer_id=raw["jobOfferWebId"],
            employer_id=raw["employerId"],
            language=(raw.get("jobOfferLanguage") or {}).get("isoCode", ""),
            initial_pub_date=pub["dateOfInitialPublicationUtc"],
            last_published=pub["lastPublishedUtc"],
            expiration_date=pub["expirationDateUtc"],
            is_active=pub["isActive"],
            job_title=attrs["jobTitle"],
            is_archive=attrs["isArchive"],
            is_withdrawn=attrs["isWithdrawn"],
            offer_url=attrs["offerAbsoluteUrl"],
            employer_name=attrs["displayEmployerName"],
            categories=_names(attrs.get("categories")),
            leading_category=leading.get("name"),
            workplaces=[w["displayAddress"] for w in attrs.get("workplaces") or []
                        if w.get("displayAddress")],
            position_levels=_names(emp.get("positionLevels")),
            entirely_remote_work=emp.get("entirelyRemoteWork", False),
            work_schedules=_names(emp.get("workSchedules")),
            work_modes=_names(emp.get("workModes")),
            contracts=contracts,
            salary_lower_limit=min(lows) if lows else None,
            salary_upper_limit=max(highs) if highs else None,
            technologies=_section_items(sections, "technologies"),
            requirements=_section_items(sections, "requirements-expected"),
            opt_requirements=_section_items(sections, "requirements-optional"),
            offered=_section_items(sections, "offered"),
            benefits=_section_items(sections, "benefits"),
            many_vacancies=flags.get("many-vacancies", False),
            immediate_employment=flags.get("immediate-employment", False),
            shift_work=flags.get("shift-work", False),
        )


if __name__ == "__main__":
    import json
    import sys

    with open(sys.argv[1], encoding="utf-8") as f:
        print(JobOffer.from_details_json(json.load(f)).model_dump_json(indent=2))