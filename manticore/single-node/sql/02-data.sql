-- 16 products. tags: 1 waterproof, 2 lightweight, 3 sale, 4 new
INSERT INTO products (id, title, description, category, brand, price, rating, stock, tags, added, embedding) VALUES
 (1,  'Trail running shoes',        'Lightweight trail shoes with a grippy sole for muddy and rocky trails', 'shoes', 'Altra',   129.90, 4.6, 12, (2),    1790000000, (0.9, 0.7, 0.0, 0.0)),
 (2,  'Road running shoes',         'Cushioned shoes for long road runs and marathon training',               'shoes', 'Hoka',    149.00, 4.8, 30, (2,4),  1790500000, (1.0, 0.2, 0.0, 0.0)),
 (3,  'Waterproof hiking boots',    'Leather boots with a waterproof membrane for wet hiking trails',         'shoes', 'Salomon', 189.00, 4.5,  8, (1),    1789000000, (0.3, 1.0, 0.0, 0.0)),
 (4,  'Running socks (3 pack)',     'Merino socks for running, no blisters on long runs',                    'apparel', 'Darn Tough', 39.00, 4.9, 120, (2,3), 1788000000, (0.8, 0.3, 0.0, 0.0)),
 (5,  'Waterproof running jacket',  'Packable rain jacket for running in wind and rain',                     'apparel', 'Patagonia', 179.00, 4.4, 15, (1,2), 1790200000, (0.7, 0.8, 0.0, 0.0)),
 (6,  'GPS running watch',          'Running watch with GPS, heart rate and trail maps',                      'electronics', 'Garmin', 349.00, 4.7, 20, (1,4), 1790600000, (0.8, 0.4, 0.9, 0.0)),
 (7,  'Wireless running headphones','Sweat-proof bone conduction headphones for runners',                    'electronics', 'Shokz', 129.00, 4.3, 40, (1,2), 1789500000, (0.6, 0.1, 0.9, 0.0)),
 (8,  'Noise cancelling headphones','Over-ear headphones for travel and the office',                         'electronics', 'Sony', 399.00, 4.8, 25, (4),    1790700000, (0.0, 0.1, 1.0, 0.0)),
 (9,  'Smartwatch',                 'Smartwatch with notifications, payments and a step counter',            'electronics', 'Apple', 429.00, 4.6, 50, (1),    1790300000, (0.4, 0.1, 1.0, 0.0)),
 (10, 'Camping stove',              'Compact gas stove for camping and backpacking trips',                   'outdoor', 'MSR', 99.00, 4.5, 18, (2),    1788500000, (0.0, 0.9, 0.0, 0.6)),
 (11, 'Insulated water bottle',     'Stainless steel bottle keeps drinks cold for 24 hours on the trail',   'outdoor', 'Hydro Flask', 45.00, 4.7, 200, (3), 1787000000, (0.3, 0.7, 0.0, 0.4)),
 (12, 'Ultralight tent',            'Two-person tent, 1.1 kg, for backpacking and bike touring',             'outdoor', 'Big Agnes', 499.00, 4.4, 5, (1,2), 1790400000, (0.0, 1.0, 0.0, 0.0)),
 (13, 'Espresso machine',           'Espresso machine with a steam wand for milk drinks',                    'kitchen', 'Breville', 699.00, 4.6, 7, (4),    1790800000, (0.0, 0.0, 0.3, 1.0)),
 (14, 'Chef knife',                 'Japanese steel chef knife, 21 cm',                                      'kitchen', 'Global', 119.00, 4.8, 35, (3),    1786000000, (0.0, 0.0, 0.0, 1.0)),
 (15, 'Cast iron skillet',          'Pre-seasoned cast iron pan for the stove, the oven and the campfire',  'kitchen', 'Lodge', 39.00, 4.7, 60, (3),    1785000000, (0.0, 0.4, 0.0, 1.0)),
 (16, 'Trail running vest',         'Hydration vest for trail runs and ultramarathons, two soft flasks',     'apparel', 'Salomon', 139.00, 4.6, 22, (2),    1790900000, (0.9, 0.8, 0.0, 0.0));

SELECT COUNT(*) AS products FROM products;
-- RT tables are durable on INSERT (binlog) and searchable immediately; no refresh interval
SELECT id, title, price FROM products WHERE id IN (1, 16);
