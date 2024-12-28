from main import app
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crypto_trades.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coin = db.Column(db.String(50), nullable=False)
    buy_amount_usdt = db.Column(db.Numeric(precision=18, scale=8))
    buy_price = db.Column(db.Numeric(precision=18, scale=8))
    coin_amount = db.Column(db.Numeric(precision=18, scale=8))
    buy_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sell_price = db.Column(db.Numeric(precision=18, scale=8))
    sell_time = db.Column(db.DateTime)
    profit_loss = db.Column(db.Numeric(precision=18, scale=8))
    profit_loss_percentage = db.Column(db.Numeric(precision=18, scale=8))
    status = db.Column(db.String(10), default='open')

    def calculate_metrics(self):
        if self.status == 'closed':
            sell_amount_usdt = self.coin_amount * self.sell_price
            self.profit_loss = sell_amount_usdt - self.buy_amount_usdt
            self.profit_loss_percentage = (self.profit_loss / self.buy_amount_usdt) * 100