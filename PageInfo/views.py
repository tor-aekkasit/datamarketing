from datetime import datetime, timedelta  # ใส่ไว้บนสุดของ views.py ด้วย
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Prefetch
from django.conf import settings
from .models import PageGroup, PageInfo, FacebookPost, FollowerHistory
from .forms import PageGroupForm, PageURLForm
from .fb_page_info import PageInfo as FBPageInfo
from .fb_page_info import PageFollowers  # ✅ เพิ่มบรรทัดนี้
from .tiktok_page_info import get_tiktok_info  # แก้เป็น import get_tiktok_info
from .ig_page_info import get_instagram_info
from .lm8_page_info import get_lemon8_info  # ✅ เพิ่มบรรทัดนี้
from .yt_page_info import get_youtube_info
from .fb_post_info import run_fb_post_scraper  # ✅ ถูก: ใช้ชื่อไฟล์ที่แท้จริง
from collections import Counter
import calendar
import re
import os
import json  # 👈 ต้อง import นี้


def clean_number(value):
    if isinstance(value, str):
        value = value.lower().replace(',', '').replace(' videos', '').replace(' views', '').replace(' subscribers', '').strip()
        if 'k' in value:
            return int(float(value.replace('k', '')) * 1_000)
        elif 'm' in value:
            return int(float(value.replace('m', '')) * 1_000_000)
        elif 'b' in value:
            return int(float(value.replace('b', '')) * 1_000_000_000)
        try:
            return int(value)
        except ValueError:
            return 0
    elif isinstance(value, (int, float)):
        return int(value)
    else:
        return 0


def add_page(request, group_id):
    group = PageGroup.objects.get(id=group_id)

    if request.method == 'POST':
        form = PageURLForm(request.POST)
        if form.is_valid():
            url = form.cleaned_data['url']
            platform = form.cleaned_data['platform']
            allowed_fields = {f.name for f in PageInfo._meta.get_fields()}

            if platform == 'facebook':
                    fb_data = FBPageInfo(url)
                    if 'page_id' in fb_data:
                        follower_data = PageFollowers(fb_data['page_id'])
                        if follower_data:
                            fb_data.update(follower_data)
                    filtered_data = {k: v for k, v in fb_data.items() if k in allowed_fields}
                    for key in ['page_likes_count', 'page_followers_count']:
                        value = filtered_data.get(key)
                        if isinstance(value, str):
                            filtered_data[key] = int(value.replace(',', ''))
                    filtered_data['platform'] = 'facebook'

                    # ✅ ดึงโพสต์ + บันทึกโพสต์
                    page_obj = PageInfo.objects.create(page_group=group, **filtered_data)

                    # ✅ ดึงโพสต์ + บันทึกโพสต์
                    try:
                        cutoff_date = datetime.now() - timedelta(days=30)
                        cookie_path = os.path.join(settings.BASE_DIR, 'PageInfo', 'cookie.json')

                        posts = run_fb_post_scraper(url, cookies_path=cookie_path, cutoff_dt=cutoff_date)

                        for post in posts or []:  # เผื่อ posts เป็น None
                            FacebookPost.objects.create(
                                page=page_obj,
                                post_id=post['post_id'],
                                post_timestamp_dt=post['post_timestamp_dt'],
                                post_timestamp_text=post['post_timestamp_text'],
                                post_content=post['post_content'],
                                post_imgs=post['post_imgs'],
                                reactions=post['reactions'],
                                comment_count=post['comment_count'],
                                share_count=post['share_count']
                            )
                    except Exception as e:
                        print("❌ Error fetching posts:", e)


            elif platform == 'tiktok':
                tiktok_data = get_tiktok_info(url)
                if tiktok_data:
                    filtered_data = {
                        'page_username': tiktok_data.get('username'),
                        'page_name': tiktok_data.get('nickname'),
                        'page_followers': tiktok_data.get('followers'),
                        'page_likes': tiktok_data.get('likes'),
                        'page_description': tiktok_data.get('bio'),
                        'profile_pic': tiktok_data.get('profile_pic'),
                        'page_url': tiktok_data.get('url'),
                        'platform': 'tiktok'
                    }
                    filtered_data = {k: v for k, v in filtered_data.items() if k in allowed_fields}
                    PageInfo.objects.create(page_group=group, **filtered_data)
                else:
                    form.add_error(None, "❌ ไม่สามารถดึงข้อมูล TikTok ได้ กรุณาตรวจสอบ URL หรือรอสักครู่")
                    return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})


            elif platform == 'instagram':

                match = re.search(r"instagram\.com/([\w\.\-]+)/?", url)

                if match:

                    username = match.group(1)

                    ig_data = get_instagram_info(username)

                    if ig_data:

                        filtered_data = {

                            'page_username': ig_data.get('username'),

                            'page_name': ig_data.get('username'),

                            'page_followers': ig_data.get('followers_count'),

                            'page_website': ig_data.get('website'),

                            'page_category': ig_data.get('category'),

                            'post_count': ig_data.get('post_count'),

                            'page_description': ig_data.get('bio'),

                            'profile_pic': ig_data.get('profile_pic'),

                            'page_url': ig_data.get('url'),

                            'platform': 'instagram'

                        }

                        filtered_data = {k: v for k, v in filtered_data.items() if k in allowed_fields}

                        PageInfo.objects.create(page_group=group, **filtered_data)

                    else:

                        form.add_error(None, "❌ ไม่สามารถดึงข้อมูล Instagram ได้ กรุณาตรวจสอบ URL หรือรอสักครู่")

                        return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})

                else:

                    form.add_error(None, "❌ URL Instagram ไม่ถูกต้อง")

                    return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})


            elif platform == 'lemon8':

                lm8_data = get_lemon8_info(url)  # ใช้ url เต็ม ไม่ต้องตัด username

                if lm8_data:

                    allowed_fields = {f.name for f in PageInfo._meta.get_fields()}

                    filtered_data = {

                        'page_username': lm8_data.get('username'),

                        'page_name': lm8_data.get('username'),

                        'page_followers': lm8_data.get('followers_count'),

                        'page_likes': lm8_data.get('likes_count'),

                        'following_count': lm8_data.get('following_count'),

                        'age': lm8_data.get('age'),

                        'page_description': lm8_data.get('bio'),

                        'page_website': lm8_data.get('website'),

                        'profile_pic': lm8_data.get('profile_pic'),

                        'page_url': lm8_data.get('url'),

                        'platform': 'lemon8'

                    }

                    filtered_data = {k: v for k, v in filtered_data.items() if k in allowed_fields}

                    PageInfo.objects.create(page_group=group, **filtered_data)

                else:

                    form.add_error(None, "❌ ไม่สามารถดึงข้อมูล Lemon8 ได้ กรุณาตรวจสอบ URL หรือรอสักครู่")

                    return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})




            elif platform == 'youtube':

                from .yt_page_info import get_youtube_info

                yt_data = get_youtube_info(url)

                if yt_data:

                    allowed_fields = {f.name for f in PageInfo._meta.get_fields()}

                    yt_data['subscribers_count'] = clean_number(yt_data.get('subscribers_count'))

                    yt_data['videos_count'] = clean_number(yt_data.get('videos_count'))

                    yt_data['total_views'] = clean_number(yt_data.get('total_views'))

                    filtered_data = {

                        'page_username': yt_data.get('username'),

                        'page_name': yt_data.get('page_name'),

                        'page_followers': yt_data.get('subscribers_count'),

                        'profile_pic': yt_data.get('profile_pic'),

                        'page_url': yt_data.get('page_url'),

                        'page_description': yt_data.get('bio'),

                        'page_address': yt_data.get('country'),

                        'page_join_date': yt_data.get('join_date'),

                        'page_videos_count': yt_data.get('videos_count'),

                        'page_total_views': yt_data.get('total_views'),

                        'page_website': yt_data.get('page_website'),

                        'platform': 'youtube'

                    }

                    filtered_data = {k: v for k, v in filtered_data.items() if k in allowed_fields}

                    PageInfo.objects.create(page_group=group, **filtered_data)

                else:

                    form.add_error(None, "❌ ไม่สามารถดึงข้อมูล YouTube ได้ กรุณาตรวจสอบ URL หรือรอสักครู่")

                    return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})

            return redirect('group_detail', group_id=group.id)


    else:
        form = PageURLForm()

    return render(request, 'PageInfo/add_page.html', {'form': form, 'group': group})




def create_group(request):
    if request.method == 'POST':
        form = PageGroupForm(request.POST)
        if form.is_valid():
            page_group = form.save()
            return redirect('group_detail', group_id=page_group.id)
    else:
        form = PageGroupForm()
    return render(request, 'PageInfo/create_group.html', {'form': form})

def group_detail(request, group_id):
    group = PageGroup.objects.get(id=group_id)
    pages = group.pages.all()  # ต้องมี related_name='pages'
    return render(request, 'PageInfo/group_detail.html', {
        'group': group,
        'pages': pages
    })


def index(request):
    page_groups = PageGroup.objects.prefetch_related('pages')
    total_groups = page_groups.count()  # นับจำนวนกลุ่มทั้งหมด
    return render(request, 'PageInfo/index.html', {
        'page_groups': page_groups,
        'total_groups': total_groups,  # ส่งจำนวนทั้งหมดไป template
    })

def sidebar_context(request):
    page_groups = PageGroup.objects.all()
    return {'page_groups_sidebar': page_groups, 'page_groups_count': page_groups.count()}

def pageview(request, page_id):
    page = get_object_or_404(PageInfo, id=page_id)

    facebook_posts = None
    facebook_posts_top10 = None
    facebook_posts_flop10 = None
    scatter_data = []  # ✅ เตรียม scatter_data นอก loop ใหญ่
    posts_by_day_data = []  # ✅ เตรียม posts_by_day_data

    if page.platform == "facebook":
        facebook_posts = FacebookPost.objects.filter(page=page).order_by('-post_timestamp_dt')

        for post in facebook_posts:
            reactions = post.reactions or {}
            if isinstance(reactions, str):
                try:
                    reactions = json.loads(reactions)
                except json.JSONDecodeError:
                    reactions = {}

            post.like_count = reactions.get("ถูกใจ", 0)
            post.comment_count = post.comment_count or 0
            post.share_count = post.share_count or 0
            post.total_engagement = sum(reactions.values()) + post.comment_count + post.share_count

            post.reach = getattr(post, 'reach_per_post', None)
            post.impressions = getattr(post, 'impressions', None)

            if post.reach and isinstance(post.reach, (int, float)) and post.reach > 0:
                rate = post.total_engagement / post.reach
                post.interaction_rate = f"{rate:.4%}"
            else:
                post.interaction_rate = "0%"
                post.reach = "-"

            if post.impressions and isinstance(post.impressions, (int, float)) and post.impressions > 0:
                ratio = post.total_engagement / post.impressions
                post.impression_per_view = f"{ratio:.4f}"
            else:
                post.impression_per_view = "-"

            post.negative_sentiment_share = "0%"

            # ✅ เตรียมข้อมูลสำหรับ Engagement Scatter Chart ใน loop เดียวกัน
            scatter_data.append({
                "x": post.post_timestamp_dt.strftime("%Y-%m-%d"),
                "y": post.total_engagement,
                "content": (post.post_content[:30] + '...') if post.post_content else "",
                "page_name": page.page_name,
                "timestamp_text": post.post_timestamp_text,
                "img": post.post_imgs[0] if post.post_imgs else None,
            })

        facebook_posts_top10 = sorted(facebook_posts, key=lambda p: p.total_engagement, reverse=True)[:10]
        facebook_posts_flop10 = sorted(facebook_posts, key=lambda p: p.total_engagement)[:10]

        # ✅ สร้างข้อมูล follower line chart จากตาราง FollowerHistory
        follower_qs = FollowerHistory.objects.filter(page=page).order_by('date')
        follower_data = [
            {"date": f.date.strftime("%b %d"), "followers": f.page_followers_count}
            for f in follower_qs if f.page_followers_count
        ]

        # ✅ เตรียม Counter สำหรับนับจำนวนโพสต์ตามวันในสัปดาห์
        weekday_counter = Counter()
        for post in facebook_posts:
            if post.post_timestamp_dt:
                weekday_name = post.post_timestamp_dt.strftime('%A')
                weekday_counter[weekday_name] += 1

        # ✅ เตรียมข้อมูล posts by day chart
        posts_by_day_data = [{"day": day, "count": weekday_counter.get(day, 0)} for day in calendar.day_name]
        bar_day_labels = list(calendar.day_name)  # ["Monday", "Tuesday", ..., "Sunday"]
        bar_day_values = [weekday_counter.get(day, 0) for day in bar_day_labels]

        def get_bar_color_by_count(count):
            color_map = {
                1: "#a2d2ff",  # ฟ้าพาสเทล
                2: "#cdb4db",  # ม่วงพาสเทล
                3: "#ffd6a5",  # เหลืองพาสเทล
                4: "#ffdac1",  # ส้มพาสเทล
                5: "#f9c6c9",  # ชมพูพาสเทล
                6: "#b5ead7",  # เขียวพาสเทล
            }
            return color_map.get(count, "#e2e2e2")  # fallback สีเทา

        bar_day_colors = [get_bar_color_by_count(weekday_counter.get(day, 0)) for day in bar_day_labels]

        # ✅ เตรียมข้อมูลสำหรับ Bubble Chart Best Times to Post
        best_times_data = []
        hour_bins = list(range(0, 24, 2))  # bin ทุก 2 ชั่วโมง: 0,2,4,...22
        heatmap_counter = {}

        for post in facebook_posts:
            if post.post_timestamp_dt:
                weekday = post.post_timestamp_dt.strftime('%A')  # Monday - Sunday
                hour = post.post_timestamp_dt.hour
                time_bin = hour_bins[hour // 2]  # ex: 9 => 8
                key = (weekday, time_bin)

                # ดึง reaction แบบแยกประเภท
                reactions = post.reactions or {}
                if isinstance(reactions, str):
                    try:
                        reactions = json.loads(reactions)
                    except json.JSONDecodeError:
                        reactions = {}

                likes = reactions.get("ถูกใจ", 0)
                comments = post.comment_count or 0
                shares = post.share_count or 0

                if key not in heatmap_counter:
                    heatmap_counter[key] = {
                        "count": 0,
                        "likes": 0,
                        "comments": 0,
                        "shares": 0,
                        "engagement": 0,
                    }

                heatmap_counter[key]["count"] += 1
                heatmap_counter[key]["likes"] += likes
                heatmap_counter[key]["comments"] += comments
                heatmap_counter[key]["shares"] += shares
                heatmap_counter[key]["engagement"] += likes + comments + shares

        # ✅ แปลงข้อมูลให้พร้อมใช้ใน Chart.js
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        best_times_bubble = []

        # ฟังก์ชันเลือกสีตามจำนวนโพสต์
        def get_color_by_count(count):
            color_map = {
                1: "#cdb4db",
                2: "#c5f6f7",
                3: "#f9c6c9",
                4: "#ffd6a5",
                5: "#FF6962",
            }
            return color_map.get(count, "#9E9E9E")  # สีเทาสำหรับ fallback

        for (day, hour), val in heatmap_counter.items():
            color = get_color_by_count(int(val["count"]))
            bubble = {
                "x": day_order.index(day),
                "y": hour,
                "r": max(4, min(20, val["count"] * 3)),
                "count": val["count"],
                "likes": val.get("likes", 0),
                "comments": val.get("comments", 0),
                "shares": val.get("shares", 0),
                "label": f"{day} {hour}:00 - {hour + 2}:00",
                "color": get_color_by_count(val["count"]),  # ✅ ใส่ตรงนี้
                "customTooltip": {
                    "line1": f"{day} {hour}:00 - {hour + 2}:00",
                    "line2": f"{val['count']} Number of posts",
                    "line3": f"{val.get('likes', 0)} Likes, {val.get('comments', 0)} Comments, {val.get('shares', 0)} Shares"
                }
            }
            best_times_bubble.append(bubble)

    return render(request, 'PageInfo/pageview.html', {
        'page': page,
        'facebook_posts': facebook_posts,
        'facebook_posts_top10': facebook_posts_top10,
        'facebook_posts_flop': facebook_posts_flop10,
        'scatter_data': scatter_data,
        'follower_data': follower_data,  # ✅ ส่งไปยังเทมเพลตด้วย
        'posts_by_day_data': posts_by_day_data,  # ✅ เพิ่มเพื่อส่งให้ Bar Chart
        # ✅ เพิ่ม 2 ตัวนี้เพื่อใช้กับ Chart.js
        'bar_day_labels': json.dumps(bar_day_labels),
        'bar_day_values': json.dumps(bar_day_values),
        'bubble_data': json.dumps(best_times_bubble),
        'bar_day_colors': json.dumps(bar_day_colors),
    })


