/**
 * Login/Register component for Amzur AI Chat.
 */
import { useEffect, useState } from 'react'

export default function LoginPage({ onLogin, onRegister, onLoginSuccess, googleLogin, authError = '' }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')
  const [isRegister, setIsRegister] = useState(false)
  const [googleReady, setGoogleReady] = useState(false)

  useEffect(() => {
    // Handle Google Sign-In callback
    window.handleGoogleSignIn = async (response) => {
      if (response.credential) {
        setError('')
        setIsLoading(true)
        try {
          await googleLogin(response.credential)
          onLoginSuccess()
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Google login failed')
        } finally {
          setIsLoading(false)
        }
      }
    }

    // Load Google OAuth script
    if (!window.google) {
      const script = document.createElement('script')
      script.src = 'https://accounts.google.com/gsi/client'
      script.async = true
      script.defer = true
      script.onload = () => {
        // Initialize Google Sign-In after script loads
        if (window.google) {
          const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID
          if (!clientId) {
            console.error('VITE_GOOGLE_CLIENT_ID is not set in .env')
            return
          }
          window.google.accounts.id.initialize({
            client_id: clientId,
            callback: window.handleGoogleSignIn,
          })
          setGoogleReady(true)
        }
      }
      script.onerror = () => {
        console.error('Failed to load Google Sign-In script')
      }
      document.head.appendChild(script)

      return () => {
        if (document.head.contains(script)) {
          document.head.removeChild(script)
        }
      }
    } else {
      setGoogleReady(true)
    }
  }, [googleLogin, onLoginSuccess])

  useEffect(() => {
    // Render Google button after initialization and when not registering
    if (window.google && googleReady && !isRegister) {
      const googleButtonDiv = document.getElementById('google-signin-button')
      if (googleButtonDiv) {
        try {
          // Clear previous button
          googleButtonDiv.innerHTML = ''
          window.google.accounts.id.renderButton(googleButtonDiv, {
            theme: 'outline',
            size: 'large',
            text: 'signin_with',
            width: '100%',
          })
        } catch (err) {
          console.warn('Google button render error:', err)
        }
      }
    }
  }, [isRegister, googleReady])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)

    try {
      if (isRegister) {
        await onRegister(email, password)
      } else {
        await onLogin(email, password)
      }
      onLoginSuccess()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setIsLoading(false)
    }
  }

  const displayedError = error || authError

  return (
    <div className="login-container">
      <main className="login-panel">
        <header className="login-header">
          <h1 className="login-title">Amzur AI Chat</h1>
          <p className="login-subtitle">Sign in with your <strong>@amzur.com</strong> account</p>
        </header>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label htmlFor="email" className="form-label">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              disabled={isLoading}
              className="form-input"
            />
          </div>

          <div className="form-group">
            <label htmlFor="password" className="form-label">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
              disabled={isLoading}
              className="form-input"
            />
          </div>

          {displayedError && <div className="error-message">{displayedError}</div>}

          <button
            type="submit"
            disabled={isLoading || !email || !password}
            className="form-button"
          >
            {isLoading ? 'Loading...' : isRegister ? 'Sign Up' : 'Sign In'}
          </button>
        </form>

        {!isRegister && (
          <div className="google-signin-container">
            <div className="divider">
              <span>or</span>
            </div>
            <div id="google-signin-button" className="google-button"></div>
            <p className="google-hint">Use your <strong>@amzur.com</strong> Google account</p>
          </div>
        )}

        <div className="login-toggle">
          <p>
            {isRegister ? 'Already have an account?' : "Don't have an account?"}
            <button
              type="button"
              onClick={() => {
                setIsRegister(!isRegister)
                setError('')
              }}
              className="toggle-button"
            >
              {isRegister ? 'Sign In' : 'Sign Up'}
            </button>
          </p>
        </div>
      </main>
    </div>
  )
}
