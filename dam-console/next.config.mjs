/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080',
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8080',
  },
  // Allow HMR WebSocket from 127.0.0.1 when the container is accessed via localhost
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
}

export default nextConfig
