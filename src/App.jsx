import { useEffect, useState } from 'react'
import ChatPage from './components/ChatPage'
import LoginPage from './components/LoginPage'
import { useAuth } from './hooks/useAuth'
import './App.css'

function App() {
  const { user, isLoading: authLoading, login, register, logout, googleLogin } = useAuth()
  const [authError, setAuthError] = useState('')

  useEffect(() => {
    // Clear errors after 5 seconds
    if (authError) {
      const timer = setTimeout(() => setAuthError(''), 5000)
      return () => clearTimeout(timer)
    }
  }, [authError])

  const handleLoginSuccess = () => {
    setAuthError('')
  }

  const handleLogin = async (email, password) => {
    try {
      await login(email, password)
      setAuthError('')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Login failed'
      setAuthError(message)
      throw error
    }
  }

  const handleRegister = async (email, password) => {
    try {
      await register(email, password)
      setAuthError('')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Registration failed'
      setAuthError(message)
      throw error
    }
  }

  const handleGoogleLogin = async (code) => {
    try {
      await googleLogin(code)
      setAuthError('')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Google login failed'
      setAuthError(message)
      throw error
    }
  }

  const handleLogout = async () => {
    await logout()
  }

  if (authLoading) {
    return (
      <div className="loading-container">
        <p>Loading...</p>
      </div>
    )
  }

  if (!user) {
    return (
      <LoginPage
        onLogin={handleLogin}
        onRegister={handleRegister}
        onLoginSuccess={handleLoginSuccess}
        googleLogin={handleGoogleLogin}
        authError={authError}
      />
    )
  }

  return <ChatPage user={user} onLogout={handleLogout} />
}

export default App
