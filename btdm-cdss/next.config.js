/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The OHIF viewer instance is deployed separately (see README §4) and
  // embedded via iframe. Allow overriding its origin per environment.
  env: {
    NEXT_PUBLIC_OHIF_VIEWER_URL: process.env.NEXT_PUBLIC_OHIF_VIEWER_URL || 'http://localhost:3001',
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1',
    NEXT_PUBLIC_USE_MOCK_API: process.env.NEXT_PUBLIC_USE_MOCK_API || 'true',
  },
};

module.exports = nextConfig;
