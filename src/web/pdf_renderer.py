"""
PDF Renderer for EMA+VWAP Strategy Tear Sheets.

Uses WeasyPrint to render Jinja2 HTML templates to PDF.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration


class TearSheetRenderer:
    """Renders strategy tear sheets to PDF using WeasyPrint."""

    def __init__(self, template_dir: str | None = None):
        if template_dir is None:
            template_dir = str(Path(__file__).parent / "web")
        self.template_dir = Path(template_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.font_config = FontConfiguration()

    def render_pdf(self, context: dict[str, Any]) -> bytes:
        """Render the tear sheet template to PDF bytes."""
        template = self.env.get_template("tear_sheet_template.html")
        html_content = template.render(**context)

        # Create PDF from HTML
        html_doc = HTML(string=html_content, base_url=str(self.template_dir))
        pdf_bytes = html_doc.write_pdf(font_config=self.font_config)

        return pdf_bytes

    def render_html(self, context: dict[str, Any]) -> str:
        """Render the tear sheet template to HTML string."""
        template = self.env.get_template("tear_sheet_template.html")
        return template.render(**context)

    def save_pdf(self, context: dict[str, Any], output_path: str | Path) -> Path:
        """Render and save PDF to file."""
        pdf_bytes = self.render_pdf(context)
        output_path = Path(output_path)
        output_path.write_bytes(pdf_bytes)
        return output_path


def build_tear_sheet_context(
    report: dict[str, Any],
    strategy_params: dict[str, Any],
    symbol: str,
    timeframe: str,
    exchange: str,
    trade_distribution: dict[str, Any] | None = None,
    equity_curves: list[dict[str, Any]] | None = None,
    monte_carlo_percentiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the context dictionary for tear sheet template rendering."""

    # Extract gate results
    gate_results = report.get("gate_results", {})

    # Extract Gate 1 metrics
    g1 = gate_results.get("gate1_backtest", {})
    g1_metrics = g1.get("metrics", {})

    # Extract Gate 3 (Monte Carlo) metrics
    g3 = gate_results.get("gate3_monte_carlo", {})
    g3_metrics = g3.get("metrics", {})

    # Build context
    context = {
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": exchange,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "status": report.get("status", "UNKNOWN"),
        "status_lower": report.get("status", "UNKNOWN").lower(),
        "overall_score": report.get("overall_score", 0),
        "gate_results": gate_results,
        "g1_metrics": g1_metrics,
        "g3_metrics": g3_metrics,
        "strategy_params": strategy_params,
        "trade_distribution": trade_distribution or {},
        "equity_curves": equity_curves or [],
        "monte_carlo_percentiles": monte_carlo_percentiles or {},
        "report_id": f"{symbol}_{timeframe}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
    }

    return context


def generate_tear_sheet_pdf(
    report: dict[str, Any],
    strategy_params: dict[str, Any],
    symbol: str,
    timeframe: str,
    exchange: str,
    trade_distribution: dict[str, Any] | None = None,
    equity_curves: list[dict[str, Any]] | None = None,
    monte_carlo_percentiles: dict[str, Any] | None = None,
) -> bytes:
    """Convenience function to generate tear sheet PDF bytes."""
    renderer = TearSheetRenderer()
    context = build_tear_sheet_context(
        report=report,
        strategy_params=strategy_params,
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        trade_distribution=trade_distribution,
        equity_curves=equity_curves,
        monte_carlo_percentiles=monte_carlo_percentiles,
    )
    return renderer.render_pdf(context)


def generate_tear_sheet_html(
    report: dict[str, Any],
    strategy_params: dict[str, Any],
    symbol: str,
    timeframe: str,
    exchange: str,
    trade_distribution: dict[str, Any] | None = None,
    equity_curves: list[dict[str, Any]] | None = None,
    monte_carlo_percentiles: dict[str, Any] | None = None,
) -> str:
    """Convenience function to generate tear sheet HTML string."""
    renderer = TearSheetRenderer()
    context = build_tear_sheet_context(
        report=report,
        strategy_params=strategy_params,
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        trade_distribution=trade_distribution,
        equity_curves=equity_curves,
        monte_carlo_percentiles=monte_carlo_percentiles,
    )
    return renderer.render_html(context)
