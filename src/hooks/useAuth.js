/**
 * Authentication context and hooks for managing login state and API calls.
 */
import { useCallback, useEffect, useState } from 'react'

const API_BASE = '/api'

async function getErrorMessage(response, fallbackMessage) {
  const contentType = response.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    const data = await response.json()
    if (typeof data?.detail === 'string') {
      return data.detail
    }
    if (data?.detail?.message) {
      return data.detail.message
    }
    if (typeof data?.message === 'string') {
      return data.message
    }
    return fallbackMessage
  }

  const text = (await response.text()).trim()
  if (!text) {
    return fallbackMessage
  }
  if (text.toLowerCase().includes('internal server error')) {
    return 'Server error. Please try again in a moment.'
  }
  return text
}

export function useAuth() {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  // Check if user is already logged in
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          credentials: 'include',
        })
        if (response.ok) {
          const userData = await response.json()
          setUser(userData)
        }
      } catch {
        // User not authenticated, which is fine
      } finally {
        setIsLoading(false)
      }
    }

    checkAuth()
  }, [])

  const login = useCallback(async (email, password) => {
    setError('')
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      })

      if (!response.ok) {
        const message = await getErrorMessage(response, 'Login failed')
        throw new Error(message)
      }

      const userData = await response.json()
      setUser(userData)
      return userData
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Login failed'
      setError(msg)
      throw err
    } finally {
      setIsLoading(false)
    }
  }, [])

  const register = useCallback(async (email, password) => {
    setError('')
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })

      if (!response.ok) {
        const message = await getErrorMessage(response, 'Registration failed')
        throw new Error(message)
      }

      // After registering, auto-login
      return await login(email, password)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Registration failed'
      setError(msg)
      throw err
    } finally {
      setIsLoading(false)
    }
  }, [login])

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      })
      setUser(null)
    } catch (err) {
      console.error('Logout error:', err)
    }
  }, [])

  const googleLogin = useCallback(async (credential) => {
    setError('')
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/auth/google/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ credential }),
      })

      if (!response.ok) {
        const message = await getErrorMessage(response, 'Google login failed')
        throw new Error(message)
      }

      const userData = await response.json()
      setUser(userData)
      return userData
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Google login failed'
      setError(msg)
      throw err
    } finally {
      setIsLoading(false)
    }
  }, [])

  return { user, isLoading, error, login, register, logout, googleLogin, setError }
}
