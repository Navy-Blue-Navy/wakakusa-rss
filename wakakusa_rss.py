import requests
import hashlib
import re
import time
import xml.etree.ElementTree as ET

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime


SOURCE_URL = "https://www.fukuyama-wakakusa.org/information/"
OUTPUT = Path(__file__).parent / "wakakusa.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    )
}

MAX_RETRIES = 3
MAX_PAGES = 100

DATE_RE = re.compile(
    r"(20\d{2})[./年]\s*(\d{1,2})[./月]\s*(\d{1,2})日?"
)

KNOWN_CATEGORIES = [
    "お知らせ",
    "職員募集",
    "調達情報",
]


# --------------------------------------------------
# HTTP取得
# --------------------------------------------------

def fetch(url):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            print(
                f"取得 {attempt}/{MAX_RETRIES}: {url}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30
            )

            print(
                "HTTP:",
                response.status_code
            )

            response.raise_for_status()

            return response

        except requests.RequestException as e:

            print(
                "取得失敗:",
                e
            )

            if attempt < MAX_RETRIES:

                print(
                    "10秒待って再試行します。"
                )

                time.sleep(10)

    return None


# --------------------------------------------------
# 既存RSS読み込み
# --------------------------------------------------

old_items = {}

if OUTPUT.exists():

    try:

        tree = ET.parse(
            OUTPUT
        )

        for item in tree.getroot().findall(
            "./channel/item"
        ):

            guid = item.findtext(
                "guid",
                ""
            )

            if not guid:
                continue

            old_items[guid] = {
                "title": item.findtext(
                    "title",
                    ""
                ),
                "link": item.findtext(
                    "link",
                    ""
                ),
                "description": item.findtext(
                    "description",
                    ""
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    ""
                ),
                "guid": guid,
            }

    except Exception as e:

        print(
            "既存RSS読み込みエラー:",
            e
        )

        print(
            "安全のため今回は更新しません。"
        )

        raise SystemExit(0)


print(
    "既存RSS件数:",
    len(old_items)
)


# --------------------------------------------------
# 1ページ目取得
# --------------------------------------------------

response = fetch(
    SOURCE_URL
)

if response is None:

    print(
        "サイトを取得できないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


first_soup = BeautifulSoup(
    response.content,
    "html.parser"
)


# --------------------------------------------------
# 最終ページ検出
# --------------------------------------------------

page_numbers = [1]

for a in first_soup.find_all(
    "a",
    href=True
):

    href = urljoin(
        SOURCE_URL,
        a["href"]
    )

    parsed = urlparse(
        href
    )

    query = parse_qs(
        parsed.query
    )

    if "page" not in query:
        continue

    for value in query["page"]:

        try:

            page_number = int(
                value
            )

            if (
                1 <= page_number <= MAX_PAGES
            ):
                page_numbers.append(
                    page_number
                )

        except ValueError:
            pass


last_page = max(
    page_numbers
)

print(
    "検出した最終ページ:",
    last_page
)


# --------------------------------------------------
# 記事解析
# --------------------------------------------------

def parse_page(soup):

    items = []

    blocks = soup.select(
        "div.news-item"
    )

    for block in blocks:

        text = block.get_text(
            " ",
            strip=True
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        ).strip()


        # ------------------------------
        # 日付
        # ------------------------------

        date_match = DATE_RE.search(
            text
        )

        if not date_match:
            continue


        year = int(
            date_match.group(1)
        )

        month = int(
            date_match.group(2)
        )

        day = int(
            date_match.group(3)
        )


        # ------------------------------
        # リンク
        # ------------------------------

        link_element = block.find(
            "a",
            href=True
        )

        if link_element is None:
            continue


        link = urljoin(
            SOURCE_URL,
            link_element["href"]
        )


        # ------------------------------
        # カテゴリ
        # ------------------------------

        category = ""

        for candidate in KNOWN_CATEGORIES:

            pattern = (
                rf"(?:^|\s)"
                rf"{re.escape(candidate)}"
                rf"(?:\s|$)"
            )

            if re.search(
                pattern,
                text
            ):

                category = candidate
                break


        # ------------------------------
        # タイトル
        # HTML上に専用要素あり
        # ------------------------------

        title_element = block.select_one(
            ".news-item-ttl"
        )

        if title_element is not None:

            title = title_element.get_text(
                " ",
                strip=True
            )

        else:

            title = link_element.get_text(
                " ",
                strip=True
            )

            title = DATE_RE.sub(
                "",
                title,
                count=1
            )

            if category:

                title = re.sub(
                    rf"^\s*{re.escape(category)}\s*",
                    "",
                    title,
                    count=1
                )


        title = re.sub(
            r"\s+",
            " ",
            title
        ).strip()


        if not title:
            continue


        # ------------------------------
        # RSS日時
        # 掲載時刻なし → 正午
        # ------------------------------

        pub_dt = datetime(
            year,
            month,
            day,
            12,
            0,
            0,
            tzinfo=JST
        )


        # ------------------------------
        # GUID
        #
        # URLだけにはしない。
        # 同じリンク先を将来複数記事が
        # 使用する場合にも対応。
        # ------------------------------

        guid_source = (
            "wakakusa|"
            + f"{year:04d}-{month:02d}-{day:02d}|"
            + category
            + "|"
            + title
            + "|"
            + link
        )

        guid = hashlib.sha256(
            guid_source.encode(
                "utf-8"
            )
        ).hexdigest()


        description = ""

        if category:

            description = (
                f"カテゴリ：{category}"
            )


        items.append({
            "title": title,
            "link": link,
            "description": description,
            "pubDate": format_datetime(
                pub_dt
            ),
            "guid": guid,
        })

    return items


# --------------------------------------------------
# 全ページ取得
# --------------------------------------------------

current_items = []


first_items = parse_page(
    first_soup
)

print(
    "1ページ目:",
    len(first_items),
    "件"
)

current_items.extend(
    first_items
)


for page in range(
    2,
    last_page + 1
):

    page_url = (
        SOURCE_URL
        + f"?page={page}"
    )

    page_response = fetch(
        page_url
    )

    if page_response is None:

        print(
            f"{page}ページ目を取得できないため、"
            "今回はRSSを更新しません。"
        )

        raise SystemExit(0)


    page_soup = BeautifulSoup(
        page_response.content,
        "html.parser"
    )


    page_items = parse_page(
        page_soup
    )


    print(
        f"{page}ページ目:",
        len(page_items),
        "件"
    )


    if len(page_items) == 0:

        print(
            f"{page}ページ目の記事が0件です。"
        )

        print(
            "サイト構造変更の可能性があるため、"
            "既存XMLを維持します。"
        )

        raise SystemExit(0)


    current_items.extend(
        page_items
    )


# --------------------------------------------------
# 安全チェック
# --------------------------------------------------

if len(current_items) < 10:

    print(
        "取得件数が異常に少ないため、"
        "既存XMLを維持します。"
    )

    raise SystemExit(0)


print(
    "全ページ取得:",
    len(current_items),
    "件"
)


# --------------------------------------------------
# 重複除去
# --------------------------------------------------

unique_items = []
seen = set()


for item in current_items:

    if item["guid"] in seen:
        continue

    seen.add(
        item["guid"]
    )

    unique_items.append(
        item
    )


current_items = unique_items


print(
    "重複除去後:",
    len(current_items),
    "件"
)


# --------------------------------------------------
# 既存RSSと統合
#
# 公式サイトから古い記事が消えても
# 一度RSSに取得した記事は保持
# --------------------------------------------------

all_items = []
seen_guids = set()


for item in current_items:

    all_items.append(
        item
    )

    seen_guids.add(
        item["guid"]
    )


for guid, item in old_items.items():

    if guid in seen_guids:
        continue

    all_items.append(
        item
    )

    seen_guids.add(
        guid
    )


# --------------------------------------------------
# 日付順
# --------------------------------------------------

def get_date(item):

    try:

        return parsedate_to_datetime(
            item["pubDate"]
        )

    except Exception:

        return datetime.min.replace(
            tzinfo=timezone.utc
        )


all_items.sort(
    key=get_date,
    reverse=True
)


# --------------------------------------------------
# RSS作成
# --------------------------------------------------

rss = ET.Element(
    "rss",
    version="2.0"
)

channel = ET.SubElement(
    rss,
    "channel"
)


ET.SubElement(
    channel,
    "title"
).text = (
    "広島県立福山若草園 お知らせ"
)


ET.SubElement(
    channel,
    "link"
).text = SOURCE_URL


ET.SubElement(
    channel,
    "description"
).text = (
    "広島県立福山若草園「お知らせ」の更新情報"
)


ET.SubElement(
    channel,
    "language"
).text = "ja"


# --------------------------------------------------
# RSS記事
# --------------------------------------------------

for item in all_items:

    element = ET.SubElement(
        channel,
        "item"
    )


    ET.SubElement(
        element,
        "title"
    ).text = item["title"]


    ET.SubElement(
        element,
        "link"
    ).text = item["link"]


    ET.SubElement(
        element,
        "description"
    ).text = item["description"]


    ET.SubElement(
        element,
        "pubDate"
    ).text = item["pubDate"]


    guid_element = ET.SubElement(
        element,
        "guid"
    )

    guid_element.set(
        "isPermaLink",
        "false"
    )

    guid_element.text = item["guid"]


# --------------------------------------------------
# XML保存
# --------------------------------------------------

tree = ET.ElementTree(
    rss
)

ET.indent(
    tree,
    space="  "
)

tree.write(
    OUTPUT,
    encoding="utf-8",
    xml_declaration=True
)


# --------------------------------------------------
# 結果表示
# --------------------------------------------------

print()
print("=" * 70)
print("RSS作成成功")
print("=" * 70)

print(
    "現在サイトから取得:",
    len(current_items),
    "件"
)

print(
    "RSS保存件数:",
    len(all_items),
    "件"
)

print(
    "保存先:",
    OUTPUT
)


print()
print("最新20件:")


for i, item in enumerate(
    all_items[:20],
    start=1
):

    print()

    print(
        f"[{i}] "
        f"{item['pubDate']} "
        f"{item['title']}"
    )

    if item["description"]:

        print(
            "    ",
            item["description"]
        )

    print(
        "    ",
        item["link"]
    )