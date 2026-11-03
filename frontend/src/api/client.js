import axios from 'axios'

// All requests go to /api; the Vite dev server (and Nginx in production) forwards them to Django.
const client = axios.create({ baseURL: '/api', timeout: 10000 })

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Token ${token}`
  return config
})

export default client

export const getHealth = () => client.get('/health').then((r) => r.data)
