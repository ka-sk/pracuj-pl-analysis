from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from pydantic import BaseModel, Field

from dimensions import (
    Dimensions
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers

def _collect(add: Callable[[dict], Any], items: Optional[Iterable[dict]]) -> list:
    """Run `add` (a Dimensions.add_* method) over items; keep the non-None keys."""
    return [k for k in map(add, items or []) if k is not None]


def _index_sections(sections: Optional[list[dict]],
                    wanted: Optional[set[str]] = None,
                    _acc: Optional[dict[str, dict]] = None) -> dict[str, dict]:
    """One dept-fisrt pass; returns {sectionType: first matching section}."""
    acc = {} if _acc is None else _acc
    for s in sections or []:
        t = s.get("sectionType")
        if t is not None and (wanted is None or t in wanted):
            acc.setdefault(t, s)
        if wanted is not None and acc.keys() >= wanted:
            return acc
        _index_sections(s.get("subSections"), wanted, acc)
    return acc


def _items_from_section(section: Optional[dict]) -> list[str]:
    """Text items of one already-located section (bullets / paragraphs / dictionary)."""
    model = (section or {}).get("model")
    if not model:
        return []
    for key in ("bullets", "paragraphs"):
        if key in model:
            return [x for x in model[key] or [] if x]
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


_SECTIONS = {
    "technologies", "responsibilities", "requirements-expected",
    "requirements-optional", "benefits", "recruitment-stages",
}

# ----------------------------------------------------------------- models

class Contract(BaseModel):
    contract_type_id: Optional[int] = None
    salary_from: Optional[float] = None
    salary_to: Optional[float] = None
    currency: Optional[str] = None
    time_unit_id: Optional[int] = None
    salary_kind: Optional[str] = None  # code, e.g. "gross" / "net"

    @classmethod
    def from_json(cls, c: dict, dims: Dimensions) -> "Contract":
        sal = c.get("salary") or {}
        return cls(
            contract_type_id=dims.add_contract_type(c),
            salary_from=sal.get("from"),
            salary_to=sal.get("to"),
            currency=(sal.get("currency") or {}).get("code"),
            time_unit_id=dims.add_time_unit(sal.get("timeUnit")),
            salary_kind=dims.add_salary_kind(sal.get("salaryKind")),
        )

class JobOffer(BaseModel):
    offer_id: int
    employer_id: int
    language: str
    offer_version: str
    initial_pub_date: datetime
    last_published: datetime
    expiration_date: datetime
    is_active: bool
 
    job_title: str
    is_archive: bool
    is_withdrawn: bool
    offer_url: str
    employer_name: str
    is_directly_from_employer: Optional[bool] = None
 
    category_ids: list[int] = Field(default_factory=list)
    leading_category_id: Optional[int] = None
    location_keys: list[str] = Field(default_factory=list)  # keys of Dimensions.locations
    position_level_ids: list[int] = Field(default_factory=list)
    entirely_remote_work: bool = False
    work_schedule_ids: list[int] = Field(default_factory=list)
    work_mode_codes: list[str] = Field(default_factory=list)
 
    searching_in_whole_poland: bool = False
    remote_recruitment: bool = False
    optional_cv: Optional[bool] = None
 
    contracts: list[Contract] = Field(default_factory=list)
 
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    opt_requirements: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    recruitment_stages: list[str] = Field(default_factory=list)
 
    many_vacancies: bool = False
    immediate_employment: bool = False
    shift_work: bool = False
 
    # ------------------------------------------------------------ parsing
    @classmethod
    def from_details_json(cls, raw: dict[str, Any]) -> tuple["JobOffer", Dimensions]:
        """Build the offer (ids only) and the dimension rows it refers to, in one pass."""
        pub = raw["publicationDetails"]
        attrs = raw["attributes"]
        emp = attrs.get("employment") or {}
        applying = attrs.get("applying") or {}
        flags = _flags(raw)
        idx = _index_sections(raw.get("sections"), _SECTIONS)
        if idx.keys() != _SECTIONS:
            logger.debug("Offer %s: sections not found: %s",
                         raw.get("jobOfferWebId"), sorted(_SECTIONS - idx.keys()))
        dims = Dimensions()
 
        offer = cls(
            offer_id=raw["jobOfferWebId"],
            employer_id=raw["employerId"],
            language=(raw.get("jobOfferLanguage") or {}).get("isoCode", ""),
            offer_version=pub["jobOfferVersion"],
            initial_pub_date=pub["dateOfInitialPublicationUtc"],
            last_published=pub["lastPublishedUtc"],
            expiration_date=pub["expirationDateUtc"],
            is_active=pub["isActive"],
            job_title=attrs["jobTitle"],
            is_archive=attrs["isArchive"],
            is_withdrawn=attrs["isWithdrawn"],
            offer_url=attrs["offerAbsoluteUrl"],
            employer_name=attrs["displayEmployerName"],
            is_directly_from_employer=raw.get("isDirectlyFromEmployer"),
            category_ids=list(dict.fromkeys(_collect(dims.add_category, attrs.get("categories")))),
            leading_category_id=dims.add_category(attrs.get("leadingCategory")),
            location_keys=list(dict.fromkeys(_collect(dims.add_location, attrs.get("workplaces")))),
            position_level_ids=_collect(dims.add_position_level, emp.get("positionLevels")),
            entirely_remote_work=emp.get("entirelyRemoteWork", False),
            work_schedule_ids=_collect(dims.add_work_schedule, emp.get("workSchedules")),
            work_mode_codes=_collect(dims.add_work_mode, emp.get("workModes")),
            searching_in_whole_poland=bool(
                ((raw.get("productAddons") or {}).get("searchingInWholePoland") or {}).get("enabled")),
            remote_recruitment=bool(applying.get("remoteRecruitment")),
            optional_cv=(applying.get("strefa") or applying.get("eRecruiter") or {}).get("optionalCv"),
            contracts=[Contract.from_json(c, dims) for c in emp.get("typesOfContracts") or []],
            technologies=_items_from_section(idx.get("technologies")),
            responsibilities=_items_from_section(idx.get("responsibilities")),
            requirements=_items_from_section(idx.get("requirements-expected")),
            opt_requirements=_items_from_section(idx.get("requirements-optional")),
            benefits=_items_from_section(idx.get("benefits")),
            recruitment_stages=_items_from_section(idx.get("recruitment-stages")),
            many_vacancies=flags.get("many-vacancies", False),
            immediate_employment=flags.get("immediate-employment", False),
            shift_work=flags.get("shift-work", False),
        )
        return offer, dims
