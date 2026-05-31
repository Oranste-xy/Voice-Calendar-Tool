import json
import os
from difflib import SequenceMatcher

# 日程数据存储文件路径
CALENDAR_DB = "calendar_db.json"

def init_calendar_db():
    """初始化存储文件，不存在则创建空列表"""
    if not os.path.exists(CALENDAR_DB):
        with open(CALENDAR_DB, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def load_all_events():
    """读取全部日程数据"""
    init_calendar_db()
    with open(CALENDAR_DB, "r", encoding="utf-8") as f:
        return json.load(f)

def save_event(uid, summary, start_iso, end_iso, reminder_minutes=0):
    """新增一条日程记录到本地文件"""
    events = load_all_events()
    events.append({
        "uid": str(uid),
        "summary": summary,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "reminder_minutes": reminder_minutes
    })
    with open(CALENDAR_DB, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

def find_event_by_title(keyword: str, target_date: str = None, threshold: float = 0.5):
    """根据标题关键词模糊查询日程"""
    
    events = load_all_events()
    keyword = keyword.strip()

    # 先做标题匹配
    results = [e for e in events if keyword in e["summary"] or e["summary"] in keyword]
    if not results:
        scored = sorted(
            [(e, SequenceMatcher(None, keyword, e["summary"]).ratio()) for e in events],
            key=lambda x: x[1], reverse=True
        )
        if scored and scored[0][1] >= threshold:
            results = [scored[0][0]]

    # 如果有多个结果且提供了时间，再按日期过滤
    if target_date and len(results) > 1:
        date_prefix = target_date[:10]
        filtered = [e for e in results if e["start_iso"][:10] == date_prefix]
        if filtered:
            results = filtered

    return results
def delete_event_by_uid(uid):
    """根据UID删除单条日程"""
    events = load_all_events()
    # 过滤掉目标UID
    new_events = [e for e in events if e["uid"] != uid]
    with open(CALENDAR_DB, "w", encoding="utf-8") as f:
        json.dump(new_events, f, ensure_ascii=False, indent=2)

def update_event_by_uid(uid, **kwargs):
    """根据UID更新日程字段
    kwargs 支持：summary, start_iso, end_iso, reminder_minutes
    """
    events = load_all_events()
    for e in events:
        if e["uid"] == uid:
            e.update(kwargs)
            break
    with open(CALENDAR_DB, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)