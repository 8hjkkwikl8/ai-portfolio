PRAGMA foreign_keys = ON;

CREATE TABLE products (
    category TEXT PRIMARY KEY,
    product_line TEXT NOT NULL,
    warranty_days INTEGER NOT NULL CHECK (warranty_days >= 0)
);

CREATE TABLE channels (
    channel TEXT PRIMARY KEY,
    channel_type TEXT NOT NULL,
    commission_rate REAL NOT NULL CHECK (commission_rate >= 0)
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT NOT NULL CHECK (order_date GLOB '????-??-??'),
    category TEXT NOT NULL REFERENCES products(category),
    region TEXT NOT NULL,
    channel TEXT NOT NULL REFERENCES channels(channel),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    revenue REAL NOT NULL CHECK (revenue >= 0)
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_category ON orders(category);
CREATE INDEX idx_orders_region ON orders(region);
CREATE INDEX idx_orders_channel ON orders(channel);
