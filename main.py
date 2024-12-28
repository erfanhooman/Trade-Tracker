import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Tuple, Optional
import aiohttp
from aiohttp import ClientTimeout
from flask import render_template, request, redirect, url_for, flash
from flask_caching import Cache
from flask import Flask

from models import Trade, db
app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trade_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 120
cache = Cache(app)
API_KEY = "CG-Tmdo5sJ3ypFPjf6BgLZJNf6T"

db.init_app(app)

class CoinGeckoAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "accept": "application/json",
            "x-cg-demo-api-key": api_key
        }
        self.last_request_time = datetime.now()
        self.min_request_interval = timedelta(milliseconds=500)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _wait_for_rate_limit(self):
        """Ensure we don't exceed rate limits"""
        now = datetime.now()
        time_since_last_request = now - self.last_request_time
        if time_since_last_request < self.min_request_interval:
            await asyncio.sleep((self.min_request_interval - time_since_last_request).total_seconds())
        self.last_request_time = datetime.now()

    async def validate_coin_symbol(self, coin_symbol: str) -> Tuple[bool, str]:
        """
        Validate coin symbol and return proper CoinGecko ID
        Returns tuple (success, result) where result is either coin_id or error message
        """
        try:
            await self._wait_for_rate_limit()
            url = f"https://api.coingecko.com/api/v3/search?query={coin_symbol}"

            async with self.session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                data = await response.json()

            coins = data.get('coins', [])
            if not coins:
                return False, f"Coin '{coin_symbol}' not found. Please check the symbol and try again."

            # Check for exact matches first
            for coin in coins:
                if coin['symbol'].lower() == coin_symbol.lower() or coin['name'].lower() == coin_symbol.lower():
                    return True, coin['id']

            # If no exact match, suggest the first result
            return False, f"Did you mean {coins[0]['name']} ({coins[0]['symbol'].upper()})? Please confirm and try again."

        except aiohttp.ClientError as e:
            return False, "Error connecting to price service. Please try again later."
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"

    async def get_coin_price(self, coin_symbol: str) -> Tuple[bool, float | str]:
        """
        Get current coin price from CoinGecko API with improved error handling
        Returns tuple (success, result) where result is either price or error message
        """
        try:
            coin_price = cache.get(coin_symbol)
            if coin_price:
                return True, coin_price

            # First validate the coin symbol
            success, result = await self.validate_coin_symbol(coin_symbol)
            if not success:
                return False, result

            coin_id = result
            await self._wait_for_rate_limit()

            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
            async with self.session.get(url, headers=self.headers) as response:
                response.raise_for_status()
                data = await response.json()

            if coin_id in data:
                cache.set(coin_symbol, data[coin_id]['usd'])
                return True, float(data[coin_id]['usd'])
            else:
                return False, "Price data not available for this coin."

        except aiohttp.ClientError as e:
            return False, "Error connecting to price service. Please try again later."
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"

    async def fetch_and_save_coin_icon(self, coin: str, app) -> Optional[str]:
        """
        Fetch the coin icon from CoinGecko API and save it locally.
        Returns the relative path to the saved icon or None if failed.
        """
        try:
            coin = coin.lower()
            icon_dir = Path(app.root_path) / 'static' / 'images' / 'coins'
            icon_path = icon_dir / f"{coin}.png"

            # Check if icon already exists
            if icon_path.exists():
                return f"images/coins/{coin}.png"

            # Validate coin symbol and get coin ID
            success, coin_id = await self.validate_coin_symbol(coin)
            if not success:
                print(f"Invalid coin symbol: {coin}")
                return None

            # Respect rate limiting
            await self._wait_for_rate_limit()

            # Fetch coin data with longer timeout for image download
            timeout = ClientTimeout(total=30)
            url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"

            async with self.session.get(url, headers=self.headers, timeout=timeout) as response:
                response.raise_for_status()
                data = await response.json()

            icon_url = data.get('image', {}).get('large')
            if not icon_url:
                print(f"Icon URL not found for coin: {coin}")
                return None

            # Download the image
            await self._wait_for_rate_limit()
            async with self.session.get(icon_url, timeout=timeout) as icon_response:
                icon_response.raise_for_status()

                # Create directory if it doesn't exist
                icon_dir.mkdir(parents=True, exist_ok=True)

                # Save the image asynchronously
                with open(icon_path, 'wb') as f:
                    while True:
                        chunk = await icon_response.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

            return f"images/coins/{coin}.png"

        except Exception as e:
            print(f"Error fetching or saving icon for {coin}: {e}")
            return None

    async def get_multiple_prices(self, coin_symbols: list[str]) -> dict[str, Tuple[bool, float | str]]:
        """
        Get prices for multiple coins in parallel
        Returns dictionary of coin_symbol -> (success, result) pairs
        """
        tasks = [self.get_coin_price(symbol) for symbol in coin_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return dict(zip(coin_symbols, results))

    async def fetch_multiple_icons(self, coins: list[str], app) -> dict[str, Optional[str]]:
        """
        Fetch multiple coin icons in parallel
        Returns dictionary mapping coin symbols to their icon paths
        """
        tasks = [self.fetch_and_save_coin_icon(coin, app) for coin in coins]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {coin: result if not isinstance(result, Exception) else None
                for coin, result in zip(coins, results)}


async def get_trade_prices(api: CoinGeckoAPI, trades):
    """Get current prices for all trades in parallel"""
    coin_symbols = [trade.coin for trade in trades]
    results = await api.get_multiple_prices(coin_symbols)
    return results


async def get_trade_icons(api: CoinGeckoAPI, trades, app):
    """Get icons for all trades in parallel"""
    coin_symbols = list(set(trade.coin for trade in trades))  # Remove duplicates
    icons = await api.fetch_multiple_icons(coin_symbols, app)
    return icons


@app.route('/')
async def index():
    open_trades = Trade.query.filter_by(status='open').all()
    closed_trades = Trade.query.filter_by(status='closed').all()

    async with CoinGeckoAPI(API_KEY) as api:
        prices_task = get_trade_prices(api, open_trades)
        icons_task = get_trade_icons(api, open_trades + closed_trades, app)
        prices, icons = await asyncio.gather(prices_task, icons_task)

    # Initialize totals as Decimal
    total_realized_profit = Decimal(0)
    total_realized_invested = Decimal(0)
    total_unrealized_profit = Decimal(0)
    total_unrealized_invested = Decimal(0)

    # Process open trades
    for trade in open_trades:
        # Convert coin name to uppercase and set icon
        trade.coin_name = trade.coin.upper()
        trade.coin_icon = icons.get(trade.coin)

        success, result = prices.get(trade.coin, (False, "Price fetch failed"))
        if success:
            # Convert price to Decimal for precise calculation
            current_price = Decimal(str(result))
            trade.current_price = current_price

            # Calculate using Decimal arithmetic
            trade.current_value = trade.coin_amount * current_price
            trade.unrealized_profit = trade.current_value - trade.buy_amount_usdt

            # Handle potential division by zero
            if trade.buy_amount_usdt != 0:
                trade.unrealized_profit_percentage = (trade.unrealized_profit / trade.buy_amount_usdt * 100)
            else:
                trade.unrealized_profit_percentage = Decimal('0')

            total_unrealized_profit += trade.unrealized_profit
            total_unrealized_invested += trade.buy_amount_usdt
        else:
            trade.current_price = None
            trade.current_value = None
            trade.unrealized_profit = None
            trade.unrealized_profit_percentage = None

    # Process closed trades
    for trade in closed_trades:
        trade.coin_name = trade.coin.upper()
        trade.coin_icon = icons.get(trade.coin)

        total_realized_profit += trade.profit_loss or Decimal('0')
        total_realized_invested += trade.buy_amount_usdt or Decimal('0')

    # Calculate ROIs with safe division
    if total_realized_invested > 0:
        realized_roi = (total_realized_profit / total_realized_invested * 100)
    else:
        realized_roi = Decimal('0')

    total_invested = total_realized_invested + total_unrealized_invested
    total_profit = total_realized_profit + total_unrealized_profit

    if total_invested > 0:
        total_roi = (total_profit / total_invested * 100)
    else:
        total_roi = Decimal('0')

    return render_template('index.html',
                           open_trades=open_trades,
                           closed_trades=closed_trades,
                           total_realized_profit=float(total_realized_profit),
                           total_realized_roi=float(realized_roi),
                           total_profit=float(total_profit),
                           total_roi=float(total_roi))



@app.route('/add_trade', methods=['GET', 'POST'])
async def add_trade():
    if request.method == 'POST':
        try:
            coin = request.form.get('coin').lower()
            trade_type = request.form.get('trade_type')
            amount = Decimal(request.form.get('amount'))

            # Get current price if not provided
            buy_price = request.form.get('buy_price')

            if not buy_price:
                async with CoinGeckoAPI(API_KEY) as api:
                    success, result = await api.get_coin_price(coin)
                    if not success:
                        flash(result, 'error')
                        return redirect(url_for('add_trade'))
                    buy_price = Decimal(str(result))
            else:
                buy_price = Decimal(buy_price)

            if trade_type == 'usdt':
                buy_amount_usdt = amount
                coin_amount = buy_amount_usdt / buy_price
            else:  # coin amount
                coin_amount = amount
                buy_amount_usdt = coin_amount * buy_price

            trade = Trade(
                coin=coin,
                buy_amount_usdt=buy_amount_usdt,
                buy_price=buy_price,
                coin_amount=coin_amount
            )

            db.session.add(trade)
            db.session.commit()

            # Fetch and save coin icon asynchronously
            async with CoinGeckoAPI(API_KEY) as api:
                await api.fetch_and_save_coin_icon(coin, app)

            flash('Trade added successfully!', 'success')
            return redirect(url_for('index'))

        except ValueError as e:
            flash('Please enter valid numbers for amount and price.', 'error')
            return redirect(url_for('add_trade'))
        except Exception as e:
            flash(f'Error adding trade: {str(e)}', 'error')
            return redirect(url_for('add_trade'))

    return render_template('add_trade.html')


@app.route('/close_trade/<int:trade_id>', methods=['GET', 'POST'])
async def close_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)

    if request.method == 'POST':
        try:
            # Get current price if not provided
            sell_price = request.form.get('sell_price')

            if not sell_price:
                async with CoinGeckoAPI(API_KEY) as api:
                    success, result = await api.get_coin_price(trade.coin)
                    if not success:
                        flash('Could not fetch current price. Please enter manually.', 'error')
                        return redirect(url_for('close_trade', trade_id=trade_id))
                    sell_price = Decimal(str(result))
            else:
                sell_price = Decimal(sell_price)

            trade.sell_price = sell_price
            trade.sell_time = datetime.utcnow()
            trade.status = 'closed'
            trade.calculate_metrics()

            db.session.commit()
            flash('Trade closed successfully!', 'success')
            return redirect(url_for('index'))

        except Exception as e:
            flash(f'Error closing trade: {str(e)}', 'error')
            return redirect(url_for('close_trade', trade_id=trade_id))

    # Get current price for display
    async with CoinGeckoAPI(API_KEY) as api:
        success, result = await api.get_coin_price(trade.coin)
        current_price = Decimal(str(result)) if success else None

        # Also fetch icon if not already present
        await api.fetch_and_save_coin_icon(trade.coin, app)

    return render_template('close_trade.html',
                           trade=trade,
                           current_price=current_price)


@app.route('/delete_trade/<int:trade_id>', methods=['POST'])
def delete_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    try:
        db.session.delete(trade)
        db.session.commit()
        flash('Trade deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting trade: {str(e)}', 'error')
    return redirect(url_for('index'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
