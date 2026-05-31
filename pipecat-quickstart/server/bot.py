#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""pipecat-quickstart - Pipecat Voice Agent (STT + LLM 阶段)

当前阶段：语音输入识别 + 大模型理解和回复（无 TTS）
后续：接入日历 API + TTS

Run the bot using::

    uv run bot.py
"""
import subprocess
import os
# 5.31 add
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from calendar_storage import init_calendar_db, load_all_events, save_event, find_event_by_title,delete_event_by_uid,update_event_by_uid
#
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.processors.aggregators.llm_context import LLMContext
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments, SmallWebRTCRunnerArguments

from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

#from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService,LiveOptions
#from pipecat.services.openai import OpenAILLMService
from pipecat.services.openai.llm import OpenAILLMService

load_dotenv(override=True)

TIMEZONE = ZoneInfo("Asia/Shanghai")
ICS_OUTPUT_DIR = "./calendar_events"
os.makedirs(ICS_OUTPUT_DIR, exist_ok=True)
# 程序启动初始化日程存储文件
init_calendar_db()
def generate_ics(summary, start_iso, end_iso=None, description="", location="", duration_minutes=60, reminder_minutes=0):
    # 处理时间
    dt_start = datetime.fromisoformat(start_iso).replace(tzinfo=TIMEZONE)
    dt_end = datetime.fromisoformat(end_iso).replace(tzinfo=TIMEZONE) if end_iso else dt_start + timedelta(minutes=duration_minutes)
    current_year = datetime.now().year
    now = datetime.now(TIMEZONE)
    # 先修正年份
    dt_start = dt_start.replace(year=current_year)
    dt_end = dt_end.replace(year=current_year)
    # 如果修正后时间已过去，自动+1年（适配年底说次年日期）
    if dt_start < now:
        dt_start = dt_start.replace(year=current_year + 1)
        dt_end = dt_end.replace(year=current_year + 1)
    fmt = "%Y%m%dT%H%M%SZ"
    stamp = datetime.utcnow().strftime(fmt)
    start_str = dt_start.astimezone(ZoneInfo("UTC")).strftime(fmt)
    end_str = dt_end.astimezone(ZoneInfo("UTC")).strftime(fmt)
    # 先单独生成 uid
    event_uid = uuid.uuid4()
    # 生成ics内容
    ics_content = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VoiceCalendar//PipecatBot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event_uid}", #使用上面定义的变量
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
        # 下面是新增提醒区块
        *([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-PT{reminder_minutes}M",
            "DESCRIPTION:日程提醒",
            "END:VALARM",
        ] if reminder_minutes > 0 else []),
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])
    # 写入ics文件
    filename = f"{dt_start.strftime('%Y%m%d_%H%M')}_{summary[:20]}.ics"
    filepath = os.path.join(ICS_OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(ics_content)
    logger.info(f"✅ .ics 生成：{filepath}")
    # ========== 新增：保存当前日程信息到本地JSON ==========
    save_event(
        uid=event_uid,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        reminder_minutes=reminder_minutes
    )
    # ======================================================
    # 生成 .ics 文件之后，自动发送邮件给用户
    qq_email = os.getenv("QQ_EMAIL")
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE")

    # 环境变量检查
    if qq_email and auth_code:
        try:
            # 使用alternative类型，支持内嵌正文
            msg = MIMEMultipart("alternative")
            msg["From"] = qq_email
            msg["To"] = qq_email
            msg["Subject"] = f"📅 新日程：{summary}"

            # ① 普通文本正文
            text_body = f"你的语音日历助手已为你创建日程：\n\n标题：{summary}\n时间：{dt_start.strftime('%Y年%m月%d日 %H:%M')}\n\n点击下方「添加到日历」即可导入日程。"
            msg.attach(MIMEText(text_body, "plain", "utf-8"))

            # ② 关键：内嵌text/calendar正文（让邮箱识别为日程邀请）
            ics_body = MIMEText(ics_content, "calendar", "utf-8")
            ics_body.replace_header("Content-Type", 'text/calendar; method=REQUEST; charset="utf-8"')
            msg.attach(ics_body)

            # ③ 同时附上ICS文件作为备份（可选）
            with open(filepath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename_attach = os.path.basename(filepath)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=("utf-8", "", filename_attach)
            )
            msg.attach(part)

            # 发送邮件
            with smtplib.SMTP_SSL("smtp.qq.com", 465) as server:
                server.login(qq_email, auth_code)
                server.sendmail(qq_email, qq_email, msg.as_string())
            logger.info(f"📧 邮件已发送至 {qq_email}，手机端可一键添加到日历")

        except Exception as e:
            logger.warning(f"❌ 邮件发送失败：{str(e)}")
    else:
        logger.info("ℹ️ 未配置QQ邮箱环境变量，跳过邮件发送")

    return os.path.abspath(filepath)

def update_ics(uid, summary, start_iso, end_iso=None, description="", location="", duration_minutes=60, reminder_minutes=0):
    dt_start = datetime.fromisoformat(start_iso).replace(tzinfo=TIMEZONE)
    dt_end = datetime.fromisoformat(end_iso).replace(tzinfo=TIMEZONE) if end_iso else dt_start + timedelta(minutes=duration_minutes)

    current_year = datetime.now().year
    now = datetime.now(TIMEZONE)
    dt_start = dt_start.replace(year=current_year)
    dt_end = dt_end.replace(year=current_year)
    if dt_start < now:
        dt_start = dt_start.replace(year=current_year + 1)
        dt_end = dt_end.replace(year=current_year + 1)

    fmt = "%Y%m%dT%H%M%SZ"
    stamp = datetime.utcnow().strftime(fmt)
    start_str = dt_start.astimezone(ZoneInfo("UTC")).strftime(fmt)
    end_str = dt_end.astimezone(ZoneInfo("UTC")).strftime(fmt)

    # 修改用 METHOD:REQUEST，复用原UID
    ics_content = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VoiceCalendar//PipecatBot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"SEQUENCE:1",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
        *([
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"TRIGGER:-PT{reminder_minutes}M",
            "DESCRIPTION:日程提醒",
            "END:VALARM",
        ] if reminder_minutes > 0 else []),
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])

    # 写入文件
    filename = f"{dt_start.strftime('%Y%m%d_%H%M')}_{summary[:20]}_update.ics"
    filepath = os.path.join(ICS_OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(ics_content)

    # 同步更新本地JSON
    update_event_by_uid(
        uid=uid,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        reminder_minutes=reminder_minutes
    )

    # 发送邮件（逻辑和原发送代码一致）
    qq_email = os.getenv("QQ_EMAIL")
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE")
    if qq_email and auth_code:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = qq_email
            msg["To"] = qq_email
            msg["Subject"] = f"📅 日程已更新：{summary}"

            text_body = f"日程已修改：\n标题：{summary}\n时间：{dt_start.strftime('%Y年%m月%d日 %H:%M')}"
            msg.attach(MIMEText(text_body, "plain", "utf-8"))

            ics_body = MIMEText(ics_content, "calendar", "utf-8")
            ics_body.replace_header("Content-Type", 'text/calendar; method=REQUEST; charset="utf-8"')
            msg.attach(ics_body)

            with open(filepath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", os.path.basename(filepath)))
            msg.attach(part)

            with smtplib.SMTP_SSL("smtp.qq.com", 465) as server:
                server.login(qq_email, auth_code)
                server.sendmail(qq_email, qq_email, msg.as_string())
            logger.info(f"📧 更新邮件已发送")
        except Exception as e:
            logger.warning(f"❌ 更新邮件发送失败：{str(e)}")

    return filepath    

def cancel_ics(uid, summary, start_iso, end_iso=None):
    dt_start = datetime.fromisoformat(start_iso).replace(tzinfo=TIMEZONE)
    dt_end = datetime.fromisoformat(end_iso).replace(tzinfo=TIMEZONE) if end_iso else dt_start

    fmt = "%Y%m%dT%H%M%SZ"
    stamp = datetime.utcnow().strftime(fmt)
    start_str = dt_start.astimezone(ZoneInfo("UTC")).strftime(fmt)
    end_str = dt_end.astimezone(ZoneInfo("UTC")).strftime(fmt)

    # 删除用 METHOD:CANCEL
    ics_content = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VoiceCalendar//PipecatBot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"SEQUENCE:1",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{summary}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])

    filename = f"{dt_start.strftime('%Y%m%d_%H%M')}_{summary[:20]}_cancel.ics"
    filepath = os.path.join(ICS_OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(ics_content)

    # 从本地JSON删除记录
    delete_event_by_uid(uid)

    # 发送取消邮件
    qq_email = os.getenv("QQ_EMAIL")
    auth_code = os.getenv("QQ_EMAIL_AUTH_CODE")
    if qq_email and auth_code:
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = qq_email
            msg["To"] = qq_email
            msg["Subject"] = f"❌ 日程已取消：{summary}"
            text_body = f"以下日程已取消：\n标题：{summary}\n时间：{dt_start.strftime('%Y年%m月%d日 %H:%M')}"
            msg.attach(MIMEText(text_body, "plain", "utf-8"))

            ics_body = MIMEText(ics_content, "calendar", "utf-8")
            ics_body.replace_header("Content-Type", 'text/calendar; method=CANCEL; charset="utf-8"')
            msg.attach(ics_body)

            with open(filepath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", os.path.basename(filepath)))
            msg.attach(part)

            with smtplib.SMTP_SSL("smtp.qq.com", 465) as server:
                server.login(qq_email, auth_code)
                server.sendmail(qq_email, qq_email, msg.as_string())
            logger.info(f"📧 取消邮件已发送")
        except Exception as e:
            logger.warning(f"❌ 取消邮件发送失败：{str(e)}")

    return filepath

# 替换你原来的 CALENDAR_TOOLS 定义
CALENDAR_TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="create_calendar_event",
            description="当用户要添加日历事件、设置提醒、创建会议或安排日程时调用此工具。",
            properties={
                "summary": {"type": "string", "description": "事件标题"},
                "start_iso": {
                    "type": "string",
                    "description": f"开始时间 ISO 格式，如 '2025-06-01T15:00:00'。当前时间参考：{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%dT%H:%M:%S')}"
                },
                "end_iso": {"type": "string", "description": "结束时间（可选）"},
                "duration_minutes": {"type": "integer", "description": "持续分钟数，默认60", "default": 60},
                "description": {"type": "string", "description": "备注（可选）"},
                "location": {"type": "string", "description": "地点（可选）"},
                # 新增：提醒时长参数
                "reminder_minutes": {
                    "type": "integer",
                    "description": "日程提前提醒分钟数，无提醒则传0。例如：提前1小时传60，提前30分钟传30",
                    "default": 0
                },    
            },
            required=["summary", "start_iso"],
        ),
        # 新增：修改日程
        FunctionSchema(
            name="update_calendar_event",
            description="当用户要修改已有日程的标题、时间、地点、提醒时调用，需指定原日程名称",
            properties={
                "target_title": {"type": "string", "description": "需要修改的原日程标题/关键词"},
                "target_date": {"type": "string", "description": "原日程的日期时间ISO格式（如'2026-06-02T10:00:00'），有多个同名日程时用于区分，可不填"},
                "summary": {"type": "string", "description": "新事件标题（可不改）"},
                "start_iso": {"type": "string", "description": "新开始时间ISO格式（可不改）"},
                "end_iso": {"type": "string", "description": "新结束时间（可选，可不改）"},
                "duration_minutes": {"type": "integer", "description": "新持续分钟数，默认60", "default": 60},
                "description": {"type": "string", "description": "新备注（可选）"},
                "location": {"type": "string", "description": "新地点（可选）"},
                "reminder_minutes": {"type": "integer", "description": "新提前提醒分钟数，无提醒传0", "default": 0},
            },
            required=["target_title"],
        ),
        # 新增：删除日程
        FunctionSchema(
            name="delete_calendar_event",
            description="当用户要删除、取消已有日程时调用，需指定原日程名称/关键词",
            properties={
                "target_title": {
                    "type": "string", 
                    "description": "需要删除的日程核心标题，只填标题名称（如'在家睡觉'），不要包含时间、语气词"
                },
                "target_date": {
                    "type": "string", 
                    "description": "日程的日期时间，ISO格式（如'2026-06-02T10:00:00'），有多个同名日程时用于区分，可不填"
                },
            },
            required=["target_title"],
        )
    ]
)
async def run_bot(transport: BaseTransport):
    """Main bot logic."""
    logger.info("Starting bot")

    # 1. STT：这里示例接入了 Deepgram 的 STT 服务，支持中文识别。
    #stt = WhisperSTTService(model="small")
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(language="zh",
            model="nova-2",)
    )   #暂时不支持中英文混用，该版本以支持中文为MVP、可以简易识别中文中携带的party等简单英文词汇，如果想要以英文语音为主，删除live_options参数即可
    # 2. LLM：GLM-4-Flash（OpenAI 兼容接口）
    llm = OpenAILLMService(
        api_key=os.getenv("GLM_API_KEY"),
        base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.ai/api/paas/v4/"),
        model=os.getenv("GLM_MODEL", "glm-4-flash"),
    )

    # 3. TTS：暂未接入，后续加入
    # tts = ...

    # 初始化对话上下文，加入 system prompt
    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个语音日历助手，帮助用户管理日程。"
                    "请用简洁自然的口语回答，不要使用 emoji、markdown 或无法朗读的符号。\n\n"
                    "当用户想添加、创建日历事件或安排日程时，调用 create_calendar_event 工具。"
                    "调用成功后告知用户：.ics 文件已生成，在程序目录的 calendar_events 文件夹中，双击可导入苹果日历。\n\n"
                    f"当前时间：{datetime.now(TIMEZONE).strftime('%Y年%m月%d日 %H:%M')}，时区：Asia/Shanghai。"
                ),
            }
        ],
        tools=CALENDAR_TOOLS,
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    async def handle_create_calendar_event(params):
        arguments = params.arguments
        logger.info(f"🔧 Tool call: create_calendar_event({arguments})")
        try:
            filepath = generate_ics(
                summary=arguments["summary"],
                start_iso=arguments["start_iso"],
                end_iso=arguments.get("end_iso"),
                description=arguments.get("description", ""),
                location=arguments.get("location", ""),
                duration_minutes=arguments.get("duration_minutes", 60),
                reminder_minutes=arguments.get("reminder_minutes", 0),
            )
            result = {"success": True, "filepath": filepath, "summary": arguments["summary"]}
        except Exception as e:
            result = {"success": False, "error": str(e)}

        await params.result_callback(result)


    # 新增：删除日程
    async def handle_delete_calendar_event(params):
        arguments = params.arguments
        logger.info(f"🔧 Tool call: delete_calendar_event({arguments})")
        try:
            target_title = arguments["target_title"]
            event_list = find_event_by_title(
                arguments["target_title"],
                target_date=arguments.get("target_date")  # ← 加这行
            )
            if not event_list:
                result = {"success": False, "error": f"未找到标题包含「{target_title}」的日程"}
            else:
                event = event_list[0]
                cancel_ics(
                    uid=event["uid"],
                    summary=event["summary"],
                    start_iso=event["start_iso"],
                    end_iso=event["end_iso"]
                )
                result = {"success": True, "summary": event["summary"]}
        except Exception as e:
            result = {"success": False, "error": str(e)}

        await params.result_callback(result)


    # 新增：修改日程
    async def handle_update_calendar_event(params):
        arguments = params.arguments
        logger.info(f"🔧 Tool call: update_calendar_event({arguments})")
        try:
            target_title = arguments["target_title"]
            event_list = find_event_by_title(
                arguments["target_title"],
                target_date=arguments.get("target_date")
            )
            if not event_list:
                result = {"success": False, "error": f"未找到标题包含「{target_title}」的日程"}
            else:
                event = event_list[0]
                # 无新参数则沿用旧数据
                new_summary = arguments.get("summary") or event["summary"]
                new_start = arguments.get("start_iso") or event["start_iso"]
                new_end = arguments.get("end_iso") or event["end_iso"]
                new_desc = arguments.get("description", "")
                new_loc = arguments.get("location", "")
                new_duration = arguments.get("duration_minutes", 60)
                new_remind = arguments.get("reminder_minutes", event["reminder_minutes"])

                filepath = update_ics(
                    uid=event["uid"],
                    summary=new_summary,
                    start_iso=new_start,
                    end_iso=new_end,
                    description=new_desc,
                    location=new_loc,
                    duration_minutes=new_duration,
                    reminder_minutes=new_remind
                )
                result = {"success": True, "filepath": filepath, "summary": new_summary}
        except Exception as e:
            result = {"success": False, "error": str(e)}

        await params.result_callback(result)

    llm.register_function("create_calendar_event", handle_create_calendar_event)
    llm.register_function("delete_calendar_event", handle_delete_calendar_event)
    llm.register_function("update_calendar_event", handle_update_calendar_event)

    # Pipeline：STT → LLM（无 TTS，LLM 文本回复通过 transport 输出）
    # 注意：没有 TTS 时，transport.output() 只传输文本帧，不会有语音输出
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            # tts,  ← 后续加入 TTS 时取消注释，同时在这里插入 tts 实例
            assistant_aggregator,
            transport.output(),  
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[],
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        # 对话开始时让 LLM 主动打招呼
        context.add_message(
            {
                "role": "user",
                "content": "你好，请简单介绍一下你自己和你能帮我做什么。",
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)

    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""

    transport = None

    match runner_args:
        case DailyRunnerArguments():
            # Daily transport（云端部署时使用）
            from pipecat.transports.daily.transport import DailyParams, DailyTransport

            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Pipecat Bot",
                params=DailyParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                ),
            )
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection

            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
