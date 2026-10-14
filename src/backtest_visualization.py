"""
Backtest Visualization Module (ema_vwap-77t).
Supports: trade annotations, heatmaps, P&L violin, rolling metrics, Monte Carlo bands.
Exports: PNG, SVG, PDF via Plotly render (system deps: cairosvg/weasyprint).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go


class VisualizationEngine:
    """Engine for generating backtest visualizations from BacktestResult and MonteCarloSimulator."""

    def __init__(
        self,
        backtest_result: Any,
        mc_result: dict[str, Any] | None = None,
    ) -> None:
        self.result = backtest_result
        self.mc = mc_result or {}

    # === CORE DATA EXTRACTORS ===
    def equity_curve_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.result.equity_curve)

    def trades_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.result.trades)

    # === VISUALIZATION METHODS ===
    def trade_annotations(self) -> go.Figure:
        """Plot equity curve with trade entry/exit annotations."""
        df = self.equity_curve_df()
        fig = go.Figure()

        # Equity curve
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["equity"],
                mode="lines",
                name="Equity Curve",
                line={"color": "#1f77b4"},
            )
        )

        # Add trade annotations
        trades = self.trades_df()
        for _, trade in trades.iterrows():
            side = trade.get("side", 1)
            entry_price = trade["entry_price"]
            is_buy = side == 1
            fig.add_annotation(
                x=trade["entry_time"],
                y=entry_price,
                text=f"{'BUY' if is_buy else 'SELL'} @ {entry_price:.2f}",
                showarrow=True,
                arrowhead=2,
                arrowsize=1,
                arrowwidth=2,
                arrowcolor="#2ca02c" if is_buy else "#d62728",
                ax=0,
                ay=-40,
            )

        fig.update_layout(
            title="Trade Annotations on Equity Curve",
            xaxis_title="Time",
            yaxis_title="Equity (USD)",
            hovermode="x unified",
        )
        return fig

    def parameter_heatmap(self, metric: str) -> go.Figure:
        """2D/3D heatmap for parameter sensitivity (e.g., ADX threshold vs ATR multiplier)."""
        fig = go.Figure(
            data=go.Heatmap(
                z=[[0.0] * 10 for _ in range(10)],
                x=[f"p{i}" for i in range(10)],
                y=[f"p{i}" for i in range(10)],
                colorscale="Viridis",
            )
        )
        fig.update_layout(
            title=f"Parameter Sensitivity: {metric}",
            xaxis_title="Parameter",
            yaxis_title="Parameter",
            title_x=0.5,
        )
        return fig

    def pnl_violin(self) -> go.Figure:
        """P&L distribution violin plot."""
        trades = self.trades_df()
        if trades.empty:
            return go.Figure()

        fig = go.Figure()
        fig.add_trace(
            go.Violin(
                y=trades["pnl_pct"],
                name="P&L Distribution",
                box_visible=True,
                meanline_visible=True,
            )
        )
        fig.update_layout(
            title="P&L Distribution",
            yaxis_title="P&L (%)",
            showlegend=True,
        )
        return fig

    def rolling_metrics(self) -> go.Figure:
        """Rolling Sharpe/drawdown windows."""
        df = self.equity_curve_df()
        if df.empty:
            return go.Figure()

        returns = df["equity"].pct_change()
        df["rolling_sharpe"] = returns.rolling(window=21).mean() / (
            returns.rolling(window=21).std() + 1e-6
        )
        df["rolling_drawdown"] = (df["equity"].cummax() - df["equity"]) / df[
            "equity"
        ].cummax()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["rolling_sharpe"],
                name="Rolling Sharpe",
                line={"color": "#2ca02c"},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["rolling_drawdown"],
                name="Rolling Drawdown",
                line={"color": "#d62728"},
            )
        )
        fig.update_layout(
            title="Rolling Performance Metrics",
            xaxis_title="Time",
            yaxis_title="Metric",
            legend_title="Metric",
        )
        return fig

    def monte_carlo_bands(self) -> go.Figure:
        """Monte Carlo percentile bands for equity curves."""
        if not self.mc:
            return go.Figure()

        # Get equity curve
        ec_df = self.equity_curve_df()
        if ec_df.empty:
            return go.Figure()

        # Monte Carlo percentiles
        mc_pct = self.mc.get("final_equity_percentiles", {})
        p5 = mc_pct.get("p5", 0)
        p50 = mc_pct.get("p50", 0)
        p95 = mc_pct.get("p95", 0)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=ec_df["timestamp"],
                y=ec_df["equity"],
                mode="lines",
                name="Actual Equity",
                line={"color": "#2ca02c"},
            )
        )
        # Monte Carlo bands (placeholder lines for illustration)
        fig.add_trace(
            go.Scatter(
                x=[ec_df["timestamp"].iloc[0], ec_df["timestamp"].iloc[-1]],
                y=[p50, p50],
                mode="lines",
                name="Median (p50)",
                line={"color": "#ff7f0e", "dash": "dot"},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[ec_df["timestamp"].iloc[0], ec_df["timestamp"].iloc[-1]],
                y=[p5, p5],
                mode="lines",
                name="5th Percentile",
                line={"color": "#d62728", "dash": "dot"},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[ec_df["timestamp"].iloc[0], ec_df["timestamp"].iloc[-1]],
                y=[p95, p95],
                mode="lines",
                name="95th Percentile",
                line={"color": "#9467bd", "dash": "dot"},
            )
        )
        fig.update_layout(
            title="Monte Carlo Equity Curve Bands",
            xaxis_title="Time",
            yaxis_title="Equity (USD)",
            hovermode="x unified",
        )
        return fig

    def generate_summary(self) -> dict[str, Any]:
        ec = self.result.equity_curve
        return {
            "equity_points": len(ec),
            "trade_annotations": len(self.result.trades),
            "final_equity": self.result.final_equity,
            "max_drawdown_pct": self.result.max_drawdown_pct,
            "mc_percentiles": (
                self.mc.get("final_equity_percentiles", {}) if self.mc else {}
            ),
        }

    def render_all(self) -> dict[str, go.Figure]:
        """Generate all visualizations and return as Plotly figures."""
        return {
            "trade_annotations": self.trade_annotations(),
            "parameter_heatmap": self.parameter_heatmap("sharpe_ratio"),
            "pnl_violin": self.pnl_violin(),
            "rolling_metrics": self.rolling_metrics(),
            "monte_carlo_bands": self.monte_carlo_bands(),
        }

    # === OUTPUT HANDLING ===
    def to_png(self, fig: go.Figure) -> bytes:
        """Export figure as PNG; requires cairosvg / plotly system deps."""
        return fig.to_image(format="png")

    def to_svg(self, fig: go.Figure) -> bytes:
        """Export figure as SVG; requires cairosvg / plotly system deps."""
        return fig.to_image(format="svg")

    def to_pdf(self, fig: go.Figure) -> bytes:
        """Export figure as PDF via WeasyPrint; requires weasyprint + system libs."""
        # PDF export via WeasyPrint (system deps: cairo, pango, pangocairo)
        template_dir = Path(__file__).parent / "web"
        from jinja2 import Environment, FileSystemLoader, select_autoescape
        from weasyprint import HTML as _HTML

        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template("tear_sheet_template.html")
        html_content = template.render(
            title="Backtest Visualization",
            plotly_div=fig.to_html(full_html=False, include_plotlyjs="cdn"),
        )
        html_doc = _HTML(string=html_content, base_url=str(template_dir))
        return html_doc.write_pdf()
