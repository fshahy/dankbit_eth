import numpy as np
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO
import logging
from odoo import http
from odoo.http import request
from . import options
from . import delta
from . import gamma
from zoneinfo import ZoneInfo
import matplotlib.pyplot as plt


_logger = logging.getLogger(__name__)

_INDEX_CACHE = {
    "timestamp": 0,
    "price": None,
}

_CACHE_TTL = 120  # in seconds

# -----------------------------
# ETH defaults (Option A)
# -----------------------------
ETH_DEFAULT_FROM = 1000.0
ETH_DEFAULT_TO = 6000.0
ETH_DEFAULT_STEPS = 10


class ChartController(http.Controller):
    @staticmethod
    def _get_today_midnight_ts():
        # Current date in UTC
        today = datetime.now(timezone.utc).date()
        midnight = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=timezone.utc)
        return int(midnight.timestamp()) * 1000

    @staticmethod
    def _get_yesterday_midnight_ts():
        # Current UTC date
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        midnight = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)
        return int(midnight.timestamp() * 1000)

    @staticmethod
    def get_midnight_ts(days_offset=0):
        tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        target_day = now + timedelta(days=-days_offset)
        midnight = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight

    @staticmethod
    def get_ts_from_hour(from_hour):
        tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        from_hour_ts = now.replace(hour=from_hour, minute=0, second=0, microsecond=0)
        return from_hour_ts

    @http.route('/help', auth='public', type='http', website=True)
    def help_page(self):
        return request.render('dankbit.dankbit_help')

    # ============================
    # CALLS
    # ============================
    @http.route([
        "/<string:instrument>/c",
        "/<string:instrument>/c/<int:minutes_ago>",
    ], type="http", auth="public", website=True)
    def chart_png_calls(self, instrument, minutes_ago=0):
        plot_title = "taker calls"
        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        last_hedging_time = icp.get_param("dankbit.last_hedging_time")
        mock_0dte = icp.get_param('dankbit.mock_0dte')
        start_from_ts = int(icp.get_param("dankbit.from_days_ago"))
        start_ts = self.get_midnight_ts(days_offset=start_from_ts)

        if last_hedging_time:
            start_ts = last_hedging_time

        if minutes_ago:
            start_ts = datetime.now() - timedelta(minutes=minutes_ago)
            plot_title = f"{plot_title} from {str(minutes_ago)} minutes ago"

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("option_type", "=", "call"),
                ("deribit_ts", ">=", start_ts),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.direction == "buy":
                obj.long_call(trade.strike, trade.price * trade.index_price)
            elif trade.direction == "sell":
                obj.short_call(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        fig, ax = obj.plot(index_price, market_deltas, market_gammas, "taker", plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)

        png_data = buf.getvalue()

        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition", f'inline; filename="{instrument}_calls.png"'),
            ("Refresh", refresh_interval),
        ]
        return request.make_response(png_data, headers=headers)
    # ============================
    # PUTS
    # ============================
    @http.route([
        "/<string:instrument>/p",
        "/<string:instrument>/p/<int:minutes_ago>",
    ], type="http", auth="public", website=True)
    def chart_png_puts(self, instrument, minutes_ago=0):
        plot_title = "taker puts"
        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        last_hedging_time = icp.get_param("dankbit.last_hedging_time")
        mock_0dte = icp.get_param('dankbit.mock_0dte')
        start_from_ts = int(icp.get_param("dankbit.from_days_ago"))
        start_ts = self.get_midnight_ts(days_offset=start_from_ts)

        if last_hedging_time:
            start_ts = last_hedging_time

        if minutes_ago:
            start_ts = datetime.now() - timedelta(minutes=minutes_ago)
            plot_title = f"{plot_title} from {str(minutes_ago)} minutes ago"

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("option_type", "=", "put"),
                ("deribit_ts", ">=", start_ts),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.direction == "buy":
                obj.long_put(trade.strike, trade.price * trade.index_price)
            elif trade.direction == "sell":
                obj.short_put(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        fig, ax = obj.plot(index_price, market_deltas, market_gammas, "taker", plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)

        png_data = buf.getvalue()

        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition", f'inline; filename="{instrument}_puts.png"'),
            ("Refresh", refresh_interval),
        ]
        return request.make_response(png_data, headers=headers)

    # ============================
    # BUYS
    # ============================
    @http.route([
        "/<string:instrument>/b",
        "/<string:instrument>/b/<int:minutes_ago>",
    ], type="http", auth="public", website=True)
    def chart_png_buys(self, instrument, minutes_ago=0):
        plot_title = "taker buys"
        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        last_hedging_time = icp.get_param("dankbit.last_hedging_time")
        mock_0dte = icp.get_param('dankbit.mock_0dte')
        start_from_ts = int(icp.get_param("dankbit.from_days_ago"))
        start_ts = self.get_midnight_ts(days_offset=start_from_ts)

        if last_hedging_time:
            start_ts = last_hedging_time

        if minutes_ago:
            start_ts = datetime.now() - timedelta(minutes=minutes_ago)
            plot_title = f"{plot_title} from {str(minutes_ago)} minutes ago"

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("direction", "=", "buy"),
                ("deribit_ts", ">=", start_ts),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.option_type == "call":
                obj.long_call(trade.strike, trade.price * trade.index_price)
            elif trade.option_type == "put":
                obj.long_put(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        fig, ax = obj.plot(index_price, market_deltas, market_gammas, "taker", plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)

        png_data = buf.getvalue()

        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition", f'inline; filename="{instrument}_buys.png"'),
            ("Refresh", refresh_interval),
        ]
        return request.make_response(png_data, headers=headers)

    # ============================
    # SELLS
    # ============================
    @http.route([
        "/<string:instrument>/s",
        "/<string:instrument>/s/<int:minutes_ago>",
    ], type="http", auth="public", website=True)
    def chart_png_sells(self, instrument, minutes_ago=0):
        plot_title = "taker sells"
        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        last_hedging_time = icp.get_param("dankbit.last_hedging_time")
        mock_0dte = icp.get_param('dankbit.mock_0dte')
        start_from_ts = int(icp.get_param("dankbit.from_days_ago"))
        start_ts = self.get_midnight_ts(days_offset=start_from_ts)

        if last_hedging_time:
            start_ts = last_hedging_time

        if minutes_ago:
            start_ts = datetime.now() - timedelta(minutes=minutes_ago)
            plot_title = f"{plot_title} from {str(minutes_ago)} minutes ago"

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("direction", "=", "sell"),
                ("deribit_ts", ">=", start_ts),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.option_type == "call":
                obj.short_call(trade.strike, trade.price * trade.index_price)
            elif trade.option_type == "put":
                obj.short_put(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        fig, ax = obj.plot(index_price, market_deltas, market_gammas, "taker", plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)

        png_data = buf.getvalue()
    
        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition", f'inline; filename="{instrument}_sells.png"'),
            ("Refresh", refresh_interval),
        ]
        return request.make_response(png_data, headers=headers)

    # ============================
    # DAY VIEW: MMV / TV / etc.
    # ============================
    @http.route([
        "/<string:instrument>/<string:view_type>",
        "/<string:instrument>/<string:view_type>/<int:minutes_ago>",
    ], type="http", auth="public", website=True)
    def chart_png_day(self, instrument, view_type, minutes_ago=0):
        # keep original for filename
        original_view_type = view_type
        plot_title = f"{original_view_type}"

        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        last_hedging_time = icp.get_param("dankbit.last_hedging_time")
        mock_0dte = icp.get_param('dankbit.mock_0dte')
        start_from_ts = int(icp.get_param("dankbit.from_days_ago"))
        start_ts = self.get_midnight_ts(days_offset=start_from_ts)

        if last_hedging_time:
            start_ts = last_hedging_time

        if minutes_ago:
            start_ts = datetime.now() - timedelta(minutes=minutes_ago)
            plot_title = f"{plot_title} from {str(minutes_ago)} minutes ago"
        else:
            plot_title = f"{plot_title} today"

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("deribit_ts", ">=", start_ts),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.option_type == "call":
                if trade.direction == "buy":
                    obj.long_call(trade.strike, trade.price * trade.index_price)
                elif trade.direction == "sell":
                    obj.short_call(trade.strike, trade.price * trade.index_price)
            elif trade.option_type == "put":
                if trade.direction == "buy":
                    obj.long_put(trade.strike, trade.price * trade.index_price)
                elif trade.direction == "sell":
                    obj.short_put(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        # normalize view_type for plotting
        vt = (original_view_type or "").lower()
        if vt in ("mmv", "mm"):
            internal_view_type = "mm"
        elif vt in ("tv", "taker"):
            internal_view_type = "taker"
        elif vt in ("be_mm", "be-mm", "bem"):
            internal_view_type = "be_mm"
        elif vt in ("be_taker", "be-taker", "bet"):
            internal_view_type = "be_taker"
        else:
            internal_view_type = original_view_type

        # IMPORTANT: do NOT pass string strike here,
        # so options.OptionStrat.plot keeps view_type logic intact.
        fig, ax = obj.plot(index_price, market_deltas, market_gammas, internal_view_type, plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        png_data = buf.getvalue()

        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition",
             f'inline; filename="{instrument}_{original_view_type}_{minutes_ago}_minutes.png"'),
            ("Refresh", refresh_interval),
        ]
        return request.make_response(png_data, headers=headers)
    # ============================
    # ALL TRADES VIEW
    # ============================
    @http.route("/<string:instrument>/<string:view_type>/a", type="http", auth="public", website=True)
    def chart_png_all(self, instrument, view_type):
        original_view_type = view_type
        plot_title = f"{original_view_type} all"
        icp = request.env['ir.config_parameter'].sudo()

        day_from_price = float(icp.get_param("dankbit.from_price", default=ETH_DEFAULT_FROM))
        day_to_price = float(icp.get_param("dankbit.to_price", default=ETH_DEFAULT_TO))
        steps = int(icp.get_param("dankbit.steps", default=ETH_DEFAULT_STEPS))
        refresh_interval = int(icp.get_param("dankbit.refresh_interval", default=60))
        mock_0dte = icp.get_param('dankbit.mock_0dte')

        trades = request.env['dankbit.trade'].sudo().search(
            domain=[
                ("name", "ilike", f"{instrument}"),
                ("is_block_trade", "=", False),
            ]
        )

        # index_price = request.env['dankbit.trade'].sudo().get_index_price()
        index_price = 0
        now = time.time()
        if _INDEX_CACHE["price"] and (now - _INDEX_CACHE["timestamp"] < _CACHE_TTL):
            index_price = _INDEX_CACHE["price"]
        else:
            index_price = request.env['dankbit.trade'].sudo().get_index_price()
            _INDEX_CACHE["price"] = index_price
            _INDEX_CACHE["timestamp"] = now

        obj = options.OptionStrat(instrument, index_price, day_from_price, day_to_price, steps)

        for trade in trades:
            if trade.option_type == "call":
                if trade.direction == "buy":
                    obj.long_call(trade.strike, trade.price * trade.index_price)
                elif trade.direction == "sell":
                    obj.short_call(trade.strike, trade.price * trade.index_price)
            elif trade.option_type == "put":
                if trade.direction == "buy":
                    obj.long_put(trade.strike, trade.price * trade.index_price)
                elif trade.direction == "sell":
                    obj.short_put(trade.strike, trade.price * trade.index_price)

        STs = np.arange(day_from_price, day_to_price, steps)
        market_deltas = delta.portfolio_delta(STs, trades, 0.05, mock_0dte)
        market_gammas = gamma.portfolio_gamma(STs, trades, 0.05, mock_0dte)

        vt = (original_view_type or "").lower()
        if vt in ("mmv", "mm"):
            internal_view_type = "mm"
        elif vt in ("tv", "taker"):
            internal_view_type = "taker"
        elif vt in ("be_mm", "be-mm", "bem"):
            internal_view_type = "be_mm"
        elif vt in ("be_taker", "be-taker", "bet"):
            internal_view_type = "be_taker"
        else:
            internal_view_type = original_view_type

        fig, ax = obj.plot(index_price, market_deltas, market_gammas, internal_view_type, plot_title)

        ax.text(
            0.01, 0.02,
            f"{len(trades)} trades",
            transform=ax.transAxes,
            fontsize=14,
        )

        buf = BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)

        png_data = buf.getvalue()

        headers = [
            ("Content-Type", "image/png"),
            ("Cache-Control", "no-cache"),
            ("Content-Disposition",
             f'inline; filename="{instrument}_{original_view_type}_all.png"'),
            ("Refresh", refresh_interval * 5),
        ]
        return request.make_response(png_data, headers=headers)