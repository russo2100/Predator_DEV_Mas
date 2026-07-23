class SlippageSimulator:
    """
    Симулирует реальные издержки (проскальзывание, спред, комиссия брокера) 
    перед отправкой боевого ордера на биржу.
    """
    def __init__(self, broker_fee_pct=0.0004, avg_spread_pts=0.002):
        self.broker_fee_pct = broker_fee_pct  # Комиссия брокера (например, Тинькофф 0.04%)
        self.avg_spread_pts = avg_spread_pts  # Средний спред по газу (в пунктах)
        
    def simulate_trade_costs(self, price, volume, target_take_profit):
        """
        Оценивает, выгодно ли входить в сделку с учетом всех издержек.
        """
        # 1. Комиссия за вход и выход
        entry_fee = price * volume * self.broker_fee_pct
        exit_fee = target_take_profit * volume * self.broker_fee_pct
        total_fees = entry_fee + exit_fee
        
        # 2. Потеря на спреде (исполнение по худшей цене)
        slippage_cost = self.avg_spread_pts * volume
        
        # 3. Чистый потенциальный профит
        gross_profit = abs(target_take_profit - price) * volume
        net_profit = gross_profit - total_fees - slippage_cost
        
        # 4. Вердикт (если комиссия съедает больше 30% профита - сделку бракуем)
        profit_ratio = net_profit / gross_profit if gross_profit > 0 else 0
        is_viable = profit_ratio > 0.7 and net_profit > 0
        
        return {
            "is_viable": is_viable,
            "gross_profit": round(gross_profit, 2),
            "total_fees_and_slippage": round(total_fees + slippage_cost, 2),
            "net_profit": round(net_profit, 2),
            "profit_retention_pct": round(profit_ratio * 100, 2)
        }
