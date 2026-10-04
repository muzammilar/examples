// 18 airports in three regions: dense routes inside a region, a few long-haul links between them.
UNWIND [
  ['FRA','Frankfurt','EU'],['CDG','Paris','EU'],['AMS','Amsterdam','EU'],['MAD','Madrid','EU'],
  ['FCO','Rome','EU'],['VIE','Vienna','EU'],
  ['JFK','New York','NA'],['ORD','Chicago','NA'],['ATL','Atlanta','NA'],['DFW','Dallas','NA'],
  ['LAX','Los Angeles','NA'],['SEA','Seattle','NA'],
  ['NRT','Tokyo','AS'],['ICN','Seoul','AS'],['SIN','Singapore','AS'],['HKG','Hong Kong','AS'],
  ['BKK','Bangkok','AS'],['DEL','Delhi','AS']
] AS a
CREATE (:Airport {code: a[0], city: a[1], region: a[2]});

// routes are directed, most come in both directions. km = great-circle distance (rounded).
UNWIND [
  ['FRA','CDG',450],['CDG','FRA',450],['FRA','AMS',365],['AMS','FRA',365],['FRA','VIE',600],
  ['VIE','FRA',600],['FRA','MAD',1420],['MAD','FRA',1420],['CDG','MAD',1065],['MAD','CDG',1065],
  ['CDG','FCO',1105],['FCO','CDG',1105],['AMS','CDG',400],['CDG','AMS',400],['FCO','VIE',765],
  ['VIE','FCO',765],['MAD','FCO',1365],
  ['JFK','ORD',1185],['ORD','JFK',1185],['JFK','ATL',1220],['ATL','JFK',1220],['ORD','DFW',1290],
  ['DFW','ORD',1290],['ATL','DFW',1170],['DFW','ATL',1170],['DFW','LAX',1990],['LAX','DFW',1990],
  ['LAX','SEA',1540],['SEA','LAX',1540],['ORD','SEA',2780],['SEA','ORD',2780],['JFK','LAX',3980],
  ['NRT','ICN',1210],['ICN','NRT',1210],['ICN','HKG',2060],['HKG','ICN',2060],['HKG','SIN',2580],
  ['SIN','HKG',2580],['SIN','BKK',1430],['BKK','SIN',1430],['BKK','DEL',2930],['DEL','BKK',2930],
  ['HKG','BKK',1690],['BKK','HKG',1690],['NRT','HKG',2900],['DEL','SIN',4150],
  ['FRA','JFK',6200],['JFK','FRA',6200],['CDG','ATL',7060],['SEA','NRT',7700],['NRT','SEA',7700],
  ['LAX','ICN',9600],['FRA','DEL',6130],['DEL','FRA',6130],['FRA','SIN',10260],['SIN','FRA',10260]
] AS r
MATCH (a:Airport {code: r[0]}), (b:Airport {code: r[1]})
CREATE (a)-[:ROUTE {km: r[2]}]->(b);

MATCH (a:Airport) WITH count(a) AS airports
MATCH ()-[r:ROUTE]->() RETURN airports, count(r) AS routes;
