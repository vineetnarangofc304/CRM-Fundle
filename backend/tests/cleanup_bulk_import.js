// Cleanup for test_bulk_import.py — run: mongosh test_database --quiet /app/backend/tests/cleanup_bulk_import.js
// Removes ONLY bulk_import leads, their messages, TEST_ tags/sources/custom fields, TEST caller users, import_jobs.
const ids = db.leads.find({bulk_import: true}, {id: 1}).toArray().map(l => l.id);
print("leads:", db.leads.deleteMany({bulk_import: true}).deletedCount);
print("messages:", db.messages.deleteMany({res_id: {$in: ids}}).deletedCount + db.messages.deleteMany({lead_id: {$in: ids}}).deletedCount);
print("tags:", db.catalogs.deleteMany({type: "tag", name: {$in: [/^TEST/, "NoSuchTag"]}}).deletedCount);
print("sources:", db.catalogs.deleteMany({type: "source_lead", name: {$in: ["TEST Src Imp", "TEST Overwrite Src"]}}).deletedCount);
print("custom_fields:", db.custom_fields.deleteMany({label: /^TEST Imp/}).deletedCount);
print("users:", db.users.deleteMany({email: /^test_caller_.*@x\.in$/}).deletedCount);
print("import_jobs:", db.import_jobs.deleteMany({}).deletedCount);
