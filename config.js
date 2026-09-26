/* =====================================================================================
 * Cheezeyverse site configuration.
 *
 * This is the ONLY file you edit to point the site at your own Supabase project. It is a
 * classic script, not a module, and every page loads it with a plain <script> tag before
 * any type="module" script, so window.CV_CONFIG already exists when the modules run.
 *
 * The anon key belongs here and is meant to be public - it is in every visitor's browser
 * either way, and Row Level Security in supabase/schema.sql is what actually protects the
 * data. The service_role key must NEVER appear in this folder; it goes in the project
 * root `.env`, which is gitignored, and only the local commissioner app reads it.
 * ===================================================================================== */

window.CV_CONFIG = {
  // Supabase dashboard -> Project Settings -> API -> Project URL
  supabaseUrl: 'https://ldybkcsmgleausdnwdmb.supabase.co',

  // Supabase dashboard -> Project Settings -> API -> Project API keys -> anon / public
  supabaseAnonKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxkeWJrY3NtZ2xlYXVzZG53ZG1iIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk3MDA5ODAsImV4cCI6MjEwNTI3Njk4MH0.9e7wohtEYuJFxqp6hhqXSQH9VF9GbpzZrAkNnLu2AsU',

  // The three FBPB3 HTML Output sites, published separately by the commissioner app
  // (commissioner/publish/restyle.py re-skins them). Standings, stats and box scores all
  // live over there; this site only owns identity, characters and skill points.
  leagueSites: {
    prep: "leagues/prep/index.htm",
    college: "leagues/college/index.htm",
    pro: "leagues/pro/index.htm",
  },

  // Cosmetic. Shown in the header and the browser tab.
  universeName: 'The Cheezeyverse',
  tagline: 'Start at 14. Make it or do not.',

  // Where the Discord invite / rules chat lives. Leave blank to hide the link.
  discordInvite: '',
};
