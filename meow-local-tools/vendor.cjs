const fs = require('node:fs');
fs.mkdirSync('web/vendor', { recursive: true });
fs.copyFileSync('node_modules/lucide/dist/umd/lucide.min.js', 'web/vendor/lucide.min.js');
fs.copyFileSync('node_modules/lucide/LICENSE', 'web/vendor/LUCIDE-LICENSE');
