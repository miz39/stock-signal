"""Stock Signal MCP Server — FastMCP stdio entrypoint."""

import os
import sys

# Add project root to path so we can import existing modules
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stock-signal")

# Register all tools
from mcp_server.tools.market import register_tools as register_market
from mcp_server.tools.analysis import register_tools as register_analysis
from mcp_server.tools.portfolio import register_tools as register_portfolio
from mcp_server.tools.trading import register_tools as register_trading
from mcp_server.tools.risk import register_tools as register_risk
from mcp_server.tools.valuation import register_tools as register_valuation

register_market(mcp)
register_analysis(mcp)
register_portfolio(mcp)
register_trading(mcp)
register_risk(mcp)
register_valuation(mcp)

if __name__ == "__main__":
    mcp.run()
