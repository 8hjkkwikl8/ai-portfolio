PRAGMA foreign_keys = ON;

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT NOT NULL CHECK (order_date GLOB '????-??-??'),
    category TEXT NOT NULL,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    revenue REAL NOT NULL CHECK (revenue >= 0)
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_category ON orders(category);
CREATE INDEX idx_orders_region ON orders(region);
CREATE INDEX idx_orders_channel ON orders(channel);
