// mongosh test_database cleanup_test_data.js  -- removes TEST_ data created by test_fundle_crm.py
const ids = db.leads.find({name: /^TEST_/}, {id: 1}).toArray().map(x => x.id);
db.leads.deleteMany({id: {$in: ids}});
["messages", "follow_ups", "audit_logs", "lead_audit", "activities", "caller_activities", "mail_messages"].forEach(c => db[c].deleteMany({lead_id: {$in: ids}}));
db.catalogs.deleteMany({type: "tag", name: /^TEST_/});
db.settings.deleteOne({key: "kpi_config"});
const dm = db.settings.findOne({key: "disposition_map"});
const m = dm.map; Object.keys(m).forEach(k => { m[k] = m[k].filter(t => !t.startsWith("TEST_")); });
db.settings.updateOne({key: "disposition_map"}, {$set: {map: m}});
db.users.deleteMany({email: /^test_/i});
print("cleaned leads " + ids.length);
