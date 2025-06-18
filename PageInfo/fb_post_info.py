import json
import re
import asyncio
from pathlib import Path
from pprint import pprint
from typing import Any, Optional, List, Tuple

from playwright.async_api import Playwright, async_playwright, Browser, Page, BrowserContext
from datetime import datetime

class FBPostScraperAsync:
    def __init__(self, cookie_file: str, headless: bool = False,
                 page_url: Optional[str] = None, cutoff_dt: datetime = None,
                 batch_size: int = 10):
        self.cookie_file = cookie_file
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.page_url = page_url
        self.cutoff_dt = cutoff_dt
        self.batch_size = batch_size

        # JavaScript snippet to fetch posts (push all, let Python filter by cutoff)
        JS_FETCH_POSTS = r"""(cutoffMs) => {
            const results = [];
            let olderReached = false;
            const containers = document.querySelectorAll('div[data-pagelet^="TimelineFeedUnit_"]');
            for (const post of containers) {
                const postLink = post.querySelector('a[href*="/posts/"]');
                if (!postLink) continue;
                const abbr = postLink.querySelector('abbr');
                let epochMs = null;
                if (abbr && abbr.dataset && abbr.dataset.utime) {
                    epochMs = parseInt(abbr.dataset.utime, 10) * 1000;
                } else {
                    const tooltip = postLink.getAttribute('aria-label');
                    if (!tooltip) continue;
                    const thaiMonths = {
                        "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
                        "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
                        "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
                    };
                    // (include your existing tooltip parsing logic here)
                    const now = Date.now();
                    const relMatch = tooltip.match(/(\d+)\s*(วินาที|นาที|ชั่วโมง|วัน)/);
                    if (relMatch) {
                        const value = parseInt(relMatch[1], 10);
                        const unit = relMatch[2];
                        if (unit === 'วินาที') {
                            epochMs = now - value * 1000;
                        } else if (unit === 'นาที') {
                            epochMs = now - value * 60 * 1000;
                        } else if (unit === 'ชั่วโมง') {
                            epochMs = now - value * 3600 * 1000;
                        } else if (unit === 'วัน') {
                            epochMs = now - value * 86400 * 1000;
                        }
                    } else {
                        const abs = tooltip.match(/(\d+)\s+([^\s]+)\s+เวลา\s+(\d{1,2}):(\d{2})\s+น\.$/);
                        if (abs) {
                            const d=parseInt(abs[1],10), m=thaiMonths[abs[2]], h=parseInt(abs[3],10), min=parseInt(abs[4],10);
                            const yr=new Date().getFullYear();
                            epochMs=new Date(yr,m-1,d,h,min).getTime();
                        }
                        else {
                            // Handle "DD Month YYYY" without time
                            const absYear = tooltip.match(/(\d+)\s+([^\s]+)\s+(\d{4})$/);
                            if (absYear) {
                                const dayY = parseInt(absYear[1], 10);
                                const monthY = thaiMonths[absYear[2]];
                                const yearY = parseInt(absYear[3], 10);
                                epochMs = new Date(yearY, monthY - 1, dayY).getTime();
                            } else {
                                // Handle "DD Month" without time, assume current year
                                const absNoYear = tooltip.match(/(\d+)\s+([^\s]+)$/);
                                if (absNoYear) {
                                    const dayN = parseInt(absNoYear[1], 10);
                                    const monthN = thaiMonths[absNoYear[2]];
                                    const yearN = new Date().getFullYear();
                                    epochMs = new Date(yearN, monthN - 1, dayN).getTime();
                                }
                            }
                        }
                    }
                }
                if (epochMs !== null) {
                    if (epochMs >= cutoffMs) {
                        // Post is within cutoff window
                        results.push({ id: postLink.href, epoch: epochMs });
                    } else {
                        olderReached = true;
                        continue;
                    }
                }
            }
            return { results, olderReached };
        }"""
        self.JS_FETCH_POSTS = JS_FETCH_POSTS

    async def _scroll_and_eval(self, page, cutoff_ms):
        # Scroll to load more posts, then run the fetch JS
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
        await page.wait_for_timeout(3000)
        return await page.evaluate(self.JS_FETCH_POSTS, cutoff_ms)

    async def _process_cookie(self) -> List[dict]:
        raw = json.loads(Path(self.cookie_file).read_text())
        for cookie in raw:
            s = cookie.get("sameSite")
            if s is None or (isinstance(s, str) and s.lower() == "no_restriction"):
                cookie["sameSite"] = "None"
            elif isinstance(s, str) and s.lower() == "lax":
                cookie["sameSite"] = "Lax"
            elif isinstance(s, str) and s.lower() == "strict":
                cookie["sameSite"] = "Strict"
        return raw

    async def _confirm_login(self, page: Page) -> Optional[str]:
        try:
            # Wait for the navigation role element with aria-label "ทางลัด"
            nav = page.get_by_role("navigation", name="ทางลัด")
            await nav.wait_for(timeout=5000)
            # The first link inside nav is the user profile; its text is the username
            profile_link = nav.get_by_role("link").first
            await profile_link.wait_for(timeout=5000)
            username = (await profile_link.inner_text()).strip()
            return username
        except Exception as e:
            print(f"[confirm_login] failed to confirm login: {e}")
            return None

    def _parse_thai_timestamp(self, text: str) -> datetime:
        thai_months = {
            "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
            "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
            "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
        }
        parts = text.split()
        try:
            # Try parsing "วัน...ที่ DD Month YYYY เวลา hh:mm น."
            if len(parts) >= 5 and parts[3].isdigit():
                day = int(parts[1])
                month_name = parts[2]
                month = thai_months.get(month_name, 0)
                year = int(parts[3])
                time_part = parts[5]
            else:
                # No year provided; use current year
                day = int(parts[1])
                month_name = parts[2]
                month = thai_months.get(month_name, 0)
                year = datetime.now().year
                time_part = parts[4]  # "hh:mm"
            hour_str, minute_str = time_part.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
            return datetime(year, month, day, hour, minute)
        except Exception:
            return datetime(1970, 1, 1)

    async def _get_post(self, page: Page, cutoff_dt: datetime, max_posts: int, seen_ids: set) -> Tuple[List[Tuple[str, datetime]], bool]:
        batch: List[Tuple[str, datetime]] = []
        cutoff_ms = 0 if cutoff_dt is None else int(cutoff_dt.timestamp() * 1000)
        older_than_cutoff = False
        empty_fetch_retries = 0
        max_empty_fetch_retries = 3

        # Initial navigation & load
        if not seen_ids:
            await page.goto(self.page_url)
        await page.wait_for_selector('div[data-pagelet^="TimelineFeedUnit_"]', timeout=5000)

        # Loop until we collect enough or hit older posts
        while len(batch) < max_posts and not older_than_cutoff:
            # data = await page.evaluate(self.JS_FETCH_POSTS, cutoff_ms)
            raw = await page.evaluate(self.JS_FETCH_POSTS, cutoff_ms)
            data = raw.get("results", [])
            if raw.get("olderReached"):
                older_than_cutoff = True
            empty_fetch_retries = 0
            if not data:
                if empty_fetch_retries < max_empty_fetch_retries:
                    empty_fetch_retries += 1
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                    await page.wait_for_timeout(2000)
                    continue
                else:
                    break

            for entry in data:
                url = entry["id"]
                dt_obj = datetime.fromtimestamp(entry["epoch"] / 1000)
                if cutoff_dt and dt_obj < cutoff_dt:
                    older_than_cutoff = True
                    break
                if url not in seen_ids:
                    batch.append((url, dt_obj))
                    seen_ids.add(url)
                    if len(batch) >= max_posts:
                        break

            if older_than_cutoff or len(batch) >= max_posts:
                break

            # Scroll and retry
            # data_retry = await self._scroll_and_eval(page, cutoff_ms)
            raw_retry = await self._scroll_and_eval(page, cutoff_ms)
            data_retry = raw_retry.get("results", [])
            if raw_retry.get("olderReached"):
                older_than_cutoff = True
            # Merge retry results same as above
            for entry in data_retry:
                url = entry["id"]
                dt_obj = datetime.fromtimestamp(entry["epoch"] / 1000)
                if cutoff_dt and dt_obj < cutoff_dt:
                    older_than_cutoff = True
                    break
                if url not in seen_ids:
                    batch.append((url, dt_obj))
                    seen_ids.add(url)
                    if len(batch) >= max_posts:
                        break

        return batch, older_than_cutoff

    async def _get_post_detail(self, context: BrowserContext, post_url: str) -> Optional[dict]:
        """
        We open a *new tab/page* for each post order to parallelize.
        """
        try:
            # print(f"[get_post_detail] Opening detail page for: {post_url}")
            detail_page = await context.new_page()
            await detail_page.goto(post_url)

            # Wait for the light‐mode container
            light_container = detail_page.locator('div.__fb-light-mode.x1n2onr6.x1vjfegm').first
            try:
                await light_container.wait_for(timeout=5000)
            except Exception as e:
                print(f"[get_post_detail] Timeout waiting for light_container on {post_url}: {e}")
                await detail_page.close()
                return None

            # Hover on the <a href="/posts/..."> to reveal timestamp tooltip
            post_link = light_container.locator('a[href*="/posts/"]').first
            await post_link.hover()

            tooltip_span = detail_page.locator('div[role="tooltip"] span.x193iq5w').first
            try:
                await tooltip_span.wait_for(timeout=5000)
            except Exception as e:
                print(f"[get_post_detail] Timeout waiting for tooltip_span on {post_url}: {e}")
                await detail_page.close()
                return None
            post_timestamp_text = (await tooltip_span.text_content()).strip()
            post_timestamp_dt = self._parse_thai_timestamp(post_timestamp_text)

            # Extract story_message
            story_locator = light_container.locator('div[data-ad-rendering-role="story_message"]').first
            try:
                await story_locator.wait_for(timeout=5000)
            except Exception as e:
                print(f"[get_post_detail] Timeout waiting for story_locator on {post_url}: {e}")
                await detail_page.close()
                return None
            post_content = (await story_locator.inner_text()).strip()

            # Collect image URLs from img tags inside <a href*="/photo/">
            post_imgs = []
            photo_imgs = await light_container.locator('a[href*="/photo/"] img').all()
            for img_elem in photo_imgs:
                src_val = await img_elem.get_attribute("src")
                if src_val:
                    post_imgs.append(src_val)

            # Extract reactions
            reactions = {}
            reaction_spans = await light_container.locator('[role="toolbar"] [aria-label]').all()
            for span in reaction_spans:
                label = await span.get_attribute("aria-label")
                if label and ":" in label:
                    reaction_type, count_text = label.split(":", 1)
                    count = int(re.sub(r"\D", "", count_text))
                    reactions[reaction_type.strip()] = count

            # Comment count
            comment_count = 0
            comments = []
            try:
                comment_element = light_container.locator('span', has_text='ความคิดเห็น').first
                comment_text = (await comment_element.text_content()).strip()
                comment_count = int(re.search(r"(\d+)", comment_text).group(1))

                if comment_count > 0:
                    comments = await self._get_post_comments(detail_page)
            except:
                pass

            # Share count
            share_count = 0
            try:
                share_element = light_container.locator('span', has_text=' แชร์').first
                share_text = (await share_element.text_content()).strip()
                share_count = int(re.search(r"(\d+)", share_text).group(1))
            except:
                pass

            # Extract just the ID portion from the URL
            raw_id = post_url.split('/posts/')[1]
            post_id = raw_id.split('?')[0]

            # print(f"[get_post_detail] Successfully fetched details for {post_id}")
            await detail_page.close()

            return {
                'post_url': post_url,
                "post_id": post_id,
                "post_timestamp_text": post_timestamp_text,
                "post_timestamp_dt": post_timestamp_dt,
                "post_content": post_content,
                "post_imgs": post_imgs,
                "reactions": reactions,
                "comment_count": comment_count,
                "share_count": share_count,
                "comments": comments,
            }

        except Exception as e:
            print(f"[get_post_detail] ERROR for {post_url}: {e}")
            try:
                await detail_page.close()
            except:
                pass
            return None

    async def _get_post_comments(self, page: Page) -> list:
        comments = []
        try:
            # Wait for and click the comments sort button
            await page.wait_for_selector('div.x6s0dn4.x78zum5.xdj266r.x14z9mp.xat24cr.x1lziwak.xe0p6wg', timeout=5000)
            await page.click('div.x6s0dn4.x78zum5.xdj266r.x14z9mp.xat24cr.x1lziwak.xe0p6wg')
            # Click 'ความคิดเห็นทั้งหมด'
            await page.wait_for_selector('div[role="menuitem"] >> text="ความคิดเห็นทั้งหมด"', timeout=5000)
            await page.click('div[role="menuitem"] >> text="ความคิดเห็นทั้งหมด"')
            # Scroll the comments container until no new content appears
            container_selector = (
                "div.x14nfmen.x1s85apg.x5yr21d.xds687c.xg01cxk"
                ".x10l6tqk.x13vifvy.x1wsgiic.x19991ni.xwji4o3"
                ".x1kky2od.x1sd63oq"
            )
            container = page.locator(container_selector).first
            await container.wait_for(timeout=5000)
            # Use the container's scrollHeight to detect new loads
            last_height = await container.evaluate("el => el.scrollHeight")
            while True:
                await container.evaluate("el => el.scrollTop = el.scrollHeight")
                await page.wait_for_timeout(1000)
                new_height = await container.evaluate("el => el.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height
        except Exception as e:
            print(f"[get_post_comments] Failed to open comments menu: {e}")
            return comments

        # Extract comment data
        comment_divs = await page.locator('div.x18xomjl.xbcz3fp').all()
        for div in comment_divs:
            try:
                image_element = div.locator('svg image').first
                profile_img = await image_element.get_attribute('xlink:href')

                # Extract commenter name
                profile_name = (await div.locator('span.x6zurak').first.text_content()).strip()
                # Extract profile URL from the same anchor
                profile_url = (await div.locator('span.xjp7ctv a').first.get_attribute('href')).split('?')[0]

                # Extract full comment text from the container
                comment_container = div.locator('div.x1lliihq.xjkvuk6.x1iorvi4').first
                comment_text = (await comment_container.inner_text()).strip()

                # Hover to reveal the absolute timestamp tooltip
                time_link = div.locator('a[href*="?comment_id="]').last
                await time_link.hover()
                # Wait briefly for the tooltip to render
                await page.wait_for_timeout(500)
                # The tooltip span uses the same “x6zurak…” classes as elsewhere
                tooltip_elem = page.locator(
                    'span.x6zurak.x18bv5gf.x184q3qc.xqxll94.x1s928wv.xhkezso.x1gmr53x.x1cpjm7i.x1fgarty.x1943h6x.x193iq5w.xeuugli.x13faqbe.x1vvkbs.x1lliihq.xzsf02u.xlh3980.xvmahel.x1x9mg3.xo1l8bm').last
                time_stamp_text = (await tooltip_elem.text_content()).strip()

                # Convert Thai timestamp string to a datetime object
                time_stamp_dt = self._parse_thai_timestamp(time_stamp_text)

                comments.append({
                    "user_name": profile_name,
                    "profile_url": profile_url,
                    "profile_img": profile_img,
                    "comment_text": comment_text,
                    "time_stamp_text": time_stamp_text,
                    "time_stamp_dt": time_stamp_dt,
                })
            except Exception as e:
                print(f"[get_post_comments] Error extracting a comment: {e}")
                continue

        return comments

    async def run(self) -> None:
        print("Starting scraper...")
        async with async_playwright() as pw:
            self.browser = await pw.chromium.launch(headless=self.headless)
            print("Browser launched.")
            self.context = await self.browser.new_context()
            cookie_list = await self._process_cookie()
            await self.context.add_cookies(cookie_list)

            # ---------------------
            # 1) Confirm login
            # ---------------------
            self.page = await self.context.new_page()
            await self.page.goto("https://www.facebook.com/")
            username = await self._confirm_login(self.page)
            print(f"Login as: {username or 'unknown'}")
            if not username:
                print("Login failed, stopping.")
                return

            # ---------------------
            # 2) Get page name
            # ---------------------
            if self.page_url:
                try:
                    await self.page.goto(self.page_url)
                    title_container = self.page.locator(
                        "div.x9f619.x1n2onr6.x1ja2u2z.x78zum5.xdt5ytf.x2lah0s.x193iq5w.x1cy8zhl.xexx8yu"
                    ).first
                    await title_container.wait_for(timeout=10000)
                    page_name = (await title_container.locator("h1.html-h1").text_content()).strip()
                    print(f"Page name: {page_name}")
                    print(f"Cutoff datetime: {self.cutoff_dt}")
                except Exception as e:
                    print(f"Failed to open Facebook Page: {e}")
                    return

                # ---------------------
                # 3) Collect posts and fetch details in batches
                # ---------------------
                seen_ids = set()
                all_results = []

                batch_index = 1
                cutoff_dt = self.cutoff_dt
                empty_batch_retries = 0
                max_empty_batch_retries = 3
                while True:
                    print(f"Collecting batch {batch_index} of posts...")
                    batch_posts, older = await self._get_post(
                        page=self.page,
                        cutoff_dt=cutoff_dt,
                        max_posts=self.batch_size,
                        seen_ids=seen_ids
                    )
                    if not batch_posts:
                        if empty_batch_retries < max_empty_batch_retries:
                            empty_batch_retries += 1
                            print(f"No posts fetched; retrying scroll ({empty_batch_retries}/{max_empty_batch_retries})")
                            await self.page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                            await self.page.wait_for_timeout(500)
                            continue
                        else:
                            print("No posts fetched after retries; exiting.")
                            break
                    # Reset retry counter when posts are fetched
                    empty_batch_retries = 0

                    print(f"Found {len(batch_posts)} posts in batch {batch_index}.")
                    print("Getting post details for this batch...")

                    # Process fetched posts...
                    tasks = [
                        self._get_post_detail(self.context, post_url)
                        for (post_url, _) in batch_posts
                    ]
                    batch_results = await asyncio.gather(*tasks)
                    for detail in batch_results:
                        if detail:
                            all_results.append(detail)
                            pprint(detail)

                    # After processing, if we hit older posts, exit
                    if older:
                        print("Reached cutoff after processing; exiting.")
                        break

                    batch_index += 1
                    # Scroll down for the next batch
                    print("Scrolling down for next batch...")
                    await self.page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                    await self.page.wait_for_timeout(500)

                print(f"Fetched all post details. Total posts: {len(all_results)}")

            # ---------------------
            # 5) Cleanup
            # ---------------------
            await self.context.close()
            await self.browser.close()
            return all_results
        print("Scraper finished.")

    def start(self):
        """Synchronous entry point to launch the async run."""
        return asyncio.run(self.run())  # ✅ ใส่ return เพื่อส่งค่ากลับจริง

def run_fb_post_scraper(url: str, cookies_path: str = 'cookie.json', cutoff_dt: datetime = None):
        scraper = FBPostScraperAsync(
            cookie_file=cookies_path,
            headless=True,
            page_url=url,
            cutoff_dt=cutoff_dt,
            batch_size=10
        )
        return scraper.start()


if __name__ == "__main__":
    scraper = FBPostScraperAsync(
        cookie_file="cookie.json",
        headless=False,
        page_url="https://www.facebook.com/skooldio",
        cutoff_dt=datetime(2025, 6, 1, 0, 0),
        # cutoff_dt= None,
        batch_size = 10
    )
    scraper.start()