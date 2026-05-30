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

import os

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
from pipecat.services.deepgram.stt import DeepgramSTTService
#from pipecat.services.openai import OpenAILLMService
from pipecat.services.openai.llm import OpenAILLMService
load_dotenv(override=True)


async def run_bot(transport: BaseTransport):
    """Main bot logic."""
    logger.info("Starting bot")

    # 1. STT：本地 Whisper（首次运行会自动下载模型，约几百 MB）
    #stt = WhisperSTTService(model="small")
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
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
                    "你是一个语音日历助手。用户说的话会经过语音识别转成文字发给你，"
                    "你的回复最终会被朗读出来，所以请用简洁自然的口语回答，"
                    "不要使用 emoji、bullet point、markdown 格式或任何无法朗读的符号。"
                ),
            }
        ]
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # Pipeline：STT → LLM（无 TTS，LLM 文本回复通过 transport 输出）
    # 注意：没有 TTS 时，transport.output() 只传输文本帧，不会有语音输出
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            # tts,  ← 后续加入 TTS 时取消注释，同时在这里插入 tts 实例
            transport.output(),
            assistant_aggregator,
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
