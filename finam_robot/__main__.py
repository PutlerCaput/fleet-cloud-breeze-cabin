"""Точка входа: `python -m finam_robot [--now] [--duration S] [--train]`.

Один event loop на всё время жизни процесса (systemd), поток исполнения —
отдельный. SIGTERM/SIGINT закрывают позицию и сессию штатно.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import logging
import os
import signal
from datetime import datetime, time

from . import kernels
from .config import TZ_MOSCOW, Settings
from .gateway import AssetInfo, FinamRestGateway, PaperBroker, PaperGateway
from .notify import TelegramNotifier
from .robot import InstitutionalContestRobot
from .runtime import GcMonitor, setup_logging, tune_runtime
from .scheduler import MoscowScheduler, in_session
from .sources import GrpcBookSource, RestPollingBookSource, SyntheticBookSource
from .watchdog import RobotWatchdog

logger = logging.getLogger("ContestRobot")


async def build(settings: Settings, gc_monitor: GcMonitor | None = None):
    s = settings
    md_gateway = None
    if s.book_source in ("rest", "grpc") or not s.dry_run:
        if not s.finam_secret or not s.account_id:
            raise SystemExit("Нужны FINAM_API_TOKEN и FINAM_ACCOUNT_ID (или BOOK_SOURCE=synthetic DRY_RUN=1)")
        md_gateway = FinamRestGateway(s.finam_secret, s.account_id)
        await md_gateway.connect()

    broker = None
    if s.dry_run:
        asset = (await md_gateway.get_asset_info(s.symbol) if md_gateway
                 else AssetInfo(tick=s.risk.tick, decimals=3, lot_size=1.0))
        broker = PaperBroker(
            equity=float(os.getenv("PAPER_EQUITY", "30000")),
            margin_per_lot=float(os.getenv("PAPER_MARGIN_PER_LOT", "1500")),
            point_value=float(os.getenv("PAPER_POINT_VALUE", "1000")),
            fee_per_lot=float(os.getenv("PAPER_FEE_PER_LOT", "1.0")),
            latency=float(os.getenv("PAPER_LATENCY", "0.02")),
        )

        async def account_factory():
            return PaperGateway(broker, asset)
    else:
        async def account_factory():
            gw = FinamRestGateway(s.finam_secret, s.account_id)
            await gw.connect()
            await gw.get_asset_info(s.symbol)  # decimals для форматирования цены
            return gw

    if s.book_source == "rest":
        def source_factory():
            return RestPollingBookSource(md_gateway, s.symbol, s.rest_poll_interval)
    elif s.book_source == "grpc":
        pb2 = importlib.import_module(os.environ["FINAM_GRPC_PB2_MODULE"])

        def source_factory():
            return GrpcBookSource(s.symbol, lambda: md_gateway.jwt, pb2.SubscribeOrderBookRequest,
                                  pb2.SubscribeOrderBookResponse, mode=s.book_stream_mode)
    elif s.book_source == "synthetic":
        def source_factory():
            return SyntheticBookSource(tick=s.risk.tick, depth=s.book_depth)
    else:
        raise SystemExit(f"BOOK_SOURCE={s.book_source}: для SDK-итератора используйте "
                         f"IteratorBookSource в своём коде (см. README)")

    notifier = TelegramNotifier(s.tg_token, s.tg_chat_id)
    robot = InstitutionalContestRobot(s, account_factory=account_factory, source_factory=source_factory,
                                      notifier=notifier, md_gateway=md_gateway, gc_monitor=gc_monitor)
    if broker is not None:
        broker.quote_fn = robot.quote
    return robot, md_gateway


async def main(args) -> None:
    settings = Settings.from_env()
    listener = setup_logging(settings.log_file)
    try:
        kernels.warmup(settings.book_depth)
        gc_monitor = GcMonitor()
        robot, md_gateway = await build(settings, gc_monitor)

        if args.train:
            await robot.weekly_learning_job()
            await asyncio.sleep(0)
            return

        watchdog = RobotWatchdog(robot)
        scheduler = MoscowScheduler()
        scheduler.add("start_trading_session", settings.session_start, robot.start_trading_session,
                      settings.trading_weekdays)
        scheduler.add("stop_trading_session", settings.session_stop, robot.stop_trading_session,
                      settings.trading_weekdays)
        scheduler.add("weekly_learning_job", time(12, 0), robot.weekly_learning_job, (5,))
        tune_runtime(settings.gil_switch_interval, settings.gc_freeze)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)

        background = [asyncio.create_task(c) for c in
                      (scheduler.run(), watchdog.check_health_loop(), robot.notifier.run())]
        now = datetime.now(TZ_MOSCOW)
        if args.now or in_session(now, settings.session_start, settings.session_stop, settings.trading_weekdays):
            await robot.start_trading_session()  # перезапуск systemd посреди дня
        if args.duration:
            loop.call_later(args.duration, stop.set)
        logger.info("Робот инициализирован (DRY_RUN=%s, источник=%s).", settings.dry_run, settings.book_source)

        await stop.wait()
        logger.info("Остановка...")
        await robot.stop_trading_session()
        await robot.notifier.flush()
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if md_gateway is not None:
            await md_gateway.close()
    finally:
        listener.stop()


def run() -> None:
    parser = argparse.ArgumentParser(prog="finam_robot")
    parser.add_argument("--now", action="store_true", help="открыть сессию сразу, не дожидаясь расписания")
    parser.add_argument("--duration", type=float, default=0.0, help="остановиться через S секунд")
    parser.add_argument("--train", action="store_true", help="только переобучить ML и выйти")
    args = parser.parse_args()
    try:
        import uvloop
    except ImportError:
        uvloop = None
    if uvloop is not None:
        uvloop.run(main(args))
    else:
        asyncio.run(main(args))


if __name__ == "__main__":
    run()
