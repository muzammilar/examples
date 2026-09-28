// Run once by `make up`: database, collections, index and a named graph.
const graphs = require("@arangodb/general-graph");
if (!db._databases().includes("demo")) db._createDatabase("demo");
db._useDatabase("demo");
if (!db._collection("people")) db._create("people");
if (!db._collection("cities")) db._create("cities");
if (!db._collection("knows")) db._createEdgeCollection("knows");
db.people.ensureIndex({ type: "persistent", fields: ["age"], name: "idx_age" });
if (!graphs._exists("social")) {
  graphs._create("social", [graphs._relation("knows", ["people"], ["people"])]);
}
print("demo: " + db._collections().filter(c => !c.name().startsWith("_")).map(c => c.name()).join(", "));
