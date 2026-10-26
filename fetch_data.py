import json
import logging
from pathlib import Path
from time import sleep

import requests
from bs4 import BeautifulSoup
from pydantic import ValidationError

from job_offer import JobOffer
from logging_config import setup_logging

logger = logging.getLogger(__name__)

USER_AGENT_CONTACT = "katarz.szczepaniak@gmail.com"
HEADER = {"User-Agent": f"PortfolioProjectResearch/0.1 (kontakt: {USER_AGENT_CONTACT})"}
BASE_URL = "https://pracuj.pl/praca"
TEST_DATA_DIR = Path("test_data")
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _next_data(url: str, header: dict) -> dict:
    response = requests.get(url, headers=header, timeout=15)
    response.raise_for_status()
    tag = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or tag.string is None:
        raise ValueError(f"No __NEXT_DATA__ in page: {url}")
    return json.loads(tag.string)["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]


def get_search_output_json(url: str, header: dict) -> list:
    return _next_data(url, header)["groupedOffers"]


def get_all_search_output(base_url: str, header: dict, limit_pages: int | None = None) -> list:
    all_urls: list[str] = []
    page_number = 1

    while limit_pages is None or page_number <= limit_pages:
        page_url = base_url if page_number == 1 else f"{base_url}?pn={page_number}"
        try:
            offers = get_search_output_json(page_url, header)
        except (requests.RequestException, ValueError, KeyError, IndexError):
            logger.exception("Failed to load search page %d (%s)", page_number, page_url)
            break
        if not offers:
            logger.info("Page %d empty - no more results", page_number)
            break
        all_urls += get_details_urls(offers)
        logger.info("Loaded page %d (%d urls so far)", page_number, len(all_urls))
        page_number += 1
        sleep(2)

    return all_urls
        


def get_details_urls(offers_json: json)-> list:
    return [element["offers"][0]["offerAbsoluteUri"] for element in offers_json]


def get_details(url: str, header: dict) -> dict:
    return _next_data(url, header)


def get_all_offers(url_list, header, limit: int | None = None) -> list[JobOffer]:
    offers: list[JobOffer] = []
    urls = url_list[:limit]

    for url in urls:
        sleep(2)
        try:
            raw = get_details(url, header)
            offers.append(raw) # TODO JobOffer.from_details_json(raw))
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            logger.warning("Fetch/structure problem for %s: %s", url, e)
        except ValidationError as e:
            logger.warning("Validation failed for %s: %s", url, e)
        logger.info("Parsed %d/%d offers", len(offers), len(urls))
    return offers

def save_to_db():
    pass


if __name__ == "__main__":
    setup_logging()

    url_list = get_all_search_output(BASE_URL, HEADER, limit_pages=1)
    (TEST_DATA_DIR / "text.txt").write_text("\n".join(url_list) + "\n", encoding="utf-8")

    jsons = get_all_offers(url_list, header=HEADER, limit=50)

    offers = [JobOffer.from_details_json(d) for d in jsons]

    requirements_path = TEST_DATA_DIR / f"requirements_list.txt"
    responsibilities_path = TEST_DATA_DIR  / f"responsibilities_list.txt"

    resp_body = []
    req_body = []


    for offer, raw_json, idx in zip(offers, jsons, range(len(offers))):

        # save to seperate files
        offer_path = TEST_DATA_DIR / f"offer{idx}.json"
        dims_path = TEST_DATA_DIR  / f"dims{idx}.json"
        json_path = TEST_DATA_DIR / f"raw{idx}.json"
        offer_path.write_text(offer[0].model_dump_json(indent=2), encoding="utf-8")
        dims_path.write_text(offer[1].model_dump_json(indent=2), encoding="utf-8")

        with open(json_path, 'w') as file:
            json.dump(raw_json, file, indent=4, ensure_ascii=False)

        resp_body += offer[0].responsibilities
        req_body += offer[0].requirements
        req_body += offer[0].opt_requirements

    req_body = "\n".join(req_body)
    resp_body = "\n".join(resp_body)
    
    requirements_path.write_text(req_body + "\n", encoding="utf-8")
    responsibilities_path.write_text(resp_body + "\n", encoding="utf-8")