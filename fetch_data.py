import requests
from bs4 import BeautifulSoup
import json
from time import sleep
import os
import pathlib
import polars as pl
from job_offer import JobOffer
import logging as log
from pydantic import ValidationError


log = log.getLogger(__name__)

USER_AGENT_CONTACT = "katarz.szczepaniak@gmail.com"
HEADER = {"User-Agent": f"PortfolioProjectResearch/0.1 (kontakt: {USER_AGENT_CONTACT})"}
BASE_URL = "https://pracuj.pl/praca"
TEST_DATA_DIR = pathlib.Path("test_data")
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_search_output_json(url: str, header: dict) -> json: 
    # get page html code 
    try:
        response = requests.get(url, headers=header)
        json_data = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__").string
        json_data = json.loads(json_data)
        # initial filtering out unnecessary tags
        json_data = json_data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["groupedOffers"]

    except Exception as e:
        raise e
    
    return json_data


def get_all_search_output(base_url: str, header: dict, limit_pages: int | None = None) -> list:
    all_urls = []
    page_number = 1

    while limit_pages is None or page_number <= limit_pages:
        offers = get_search_output_json(
            base_url if page_number == 1 else f"{base_url}?pn={page_number}", header
        )
        if not offers:          # empty page = no more results
            break
        all_urls += get_details_urls(offers)
        print(f"Loaded {page_number}. page")
        page_number += 1
        sleep(2)

    return all_urls
        


def get_details_urls(offers_json: json)-> list:
    urls_list = []
    for element in offers_json:
        urls_list.append(element["offers"][0]["offerAbsoluteUri"])
    return urls_list


def get_details(url: str, header: dict) -> dict:
    response = requests.get(url, headers=header, timeout=15)
    response.raise_for_status()                     # 403/429/500 -> HTTPError

    tag = BeautifulSoup(response.text, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or tag.string is None:
        raise ValueError(f"No __NEXT_DATA__ in page: {url}")

    data = json.loads(tag.string)
    return data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]


def get_all_offers(url_list, header, limit: int | None = None) -> list[JobOffer]:
    offers = []
    for url in url_list[:limit]:
        sleep(2)
        try:
            raw = get_details(url, header)
            offers.append(raw) # TODO JobOffer.from_details_json(raw))
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            log.warning("Fetch/structure problem for %s: %s", url, e)
        except ValidationError as e:
            log.warning("Validation failed for %s: %s", url, e)
    print(f"Parsed {len(offers)}/{len(url_list[:limit])} offers")
    return offers

def save_to_db():
    pass


if __name__ == "__main__":
    url_list = get_all_search_output(BASE_URL, HEADER, limit_pages=1)

    with open("test_data/text.txt", "w", encoding="utf-8") as f:
        f.write('\n'.join(url_list) + '\n')

    offers = get_all_offers(url_list, header=HEADER, limit=3)


    offers = [JobOffer.from_details_json(d) for d in offers]

    for element, idx in zip(offers, range(len(offers))):
        with open(f"test_data/details{idx}.json", "w", encoding="utf-8") as f:
            json.dump(element, f, indent=4, ensure_ascii=False)
