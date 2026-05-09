# Run this as a one-off debug script in your project root
import asyncio
from datetime import date, timedelta
from shipping_forecast.data_provider import ExcelDataProvider

async def debug():
    p = ExcelDataProvider()
    end = date.today()
    start = date(2016, 1, 1)  # instead of end - timedelta(days=730)
    lane = "Australasia & Oceania to Europe Price Index (2026)"
    try:
        pts = await p.get_historical_rates(lane, start, end)
        print(f"Got {len(pts)} points")
        if pts:
            print("First:", pts[0])
            print("Last: ", pts[-1])
        else:
            print("EMPTY")
    except Exception as e:
        print("ERROR:", e)

asyncio.run(debug())