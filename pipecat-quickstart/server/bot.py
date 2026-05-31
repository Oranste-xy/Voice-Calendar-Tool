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

def generate_ics(summary, start_iso, end_iso=None, description="", location="", duration_minutes=60):
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
    # 生成ics内容
    ics_content = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VoiceCalendar//PipecatBot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
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
            },
            required=["summary", "start_iso"],
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
            )
            result = {"success": True, "filepath": filepath, "summary": arguments["summary"]}
        except Exception as e:
            result = {"success": False, "error": str(e)}

        await params.result_callback(result)

    llm.register_function("create_calendar_event", handle_create_calendar_event)

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
