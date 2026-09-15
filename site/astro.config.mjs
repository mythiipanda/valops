import { defineConfig } from 'astro/config';
import react from '@astrojs/react';

export default defineConfig({ base: '/valops/', integrations: [react()] });
