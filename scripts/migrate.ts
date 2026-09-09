import { loadEnv } from '../src/config/env.js';
import { openDatabase } from '../src/db/client.js';

const env = loadEnv();
const connection = openDatabase(env.DATABASE_PATH);
connection.close();
console.log(`Migrated and initialized ${env.DATABASE_PATH}`);
