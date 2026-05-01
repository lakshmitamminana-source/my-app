/**
 * Main chat interface with thread management.
 */
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { useChat } from '../hooks/useChat'

export default function ChatPage({ user, onLogout }) {
  const {
    threads,
    activeThreadId,
    messages,
    isLoading,
    error,
    loadThreads,
    loadThread,
    createThread,
    sendMessage,
    deleteThread,
    updateThread,
    setError,
  } = useChat()

  const [input, setInput] = useState('')
  const [showSidebar, setShowSidebar] = useState(true)
  const [isCreatingThread, setIsCreatingThread] = useState(false)
  const [editingThreadId, setEditingThreadId] = useState(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [isSavingTitle, setIsSavingTitle] = useState(false)
  const messagesEndRef = useRef(null)

  // Load threads on mount
  useEffect(() => {
    loadThreads()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleNewChat = async () => {
    setError('')
    setIsCreatingThread(true)
    try {
      await createThread()
    } catch {
      setError('Failed to create new chat')
    } finally {
      setIsCreatingThread(false)
    }
  }

  const handleSelectThread = (threadId) => {
    loadThread(threadId)
  }

  const handleDeleteThread = async (e, threadId) => {
    e.stopPropagation()
    if (window.confirm('Delete this chat?')) {
      await deleteThread(threadId)
    }
  }

  const handleStartEditTitle = (e, threadId, currentTitle) => {
    e.stopPropagation()
    setEditingThreadId(threadId)
    setEditingTitle(currentTitle)
  }

  const handleCancelEditTitle = () => {
    setEditingThreadId(null)
    setEditingTitle('')
  }

  const handleSaveEditTitle = async (e, threadId) => {
    e.stopPropagation()
    const nextTitle = editingTitle.trim()

    if (!nextTitle) {
      setError('Title cannot be empty')
      return
    }

    setIsSavingTitle(true)
    try {
      await updateThread(threadId, { title: nextTitle })
      handleCancelEditTitle()
    } catch {
      setError('Failed to update chat title')
    } finally {
      setIsSavingTitle(false)
    }
  }

  const handleSendMessage = async (e) => {
    e.preventDefault()
    if (!input.trim() || isLoading || !activeThreadId) return

    const userMessage = input
    setInput('')

    try {
      // Convert messages to proper format for API
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))

      await sendMessage(userMessage, history)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message')
    }
  }

  const handleLogout = async () => {
    if (window.confirm('Are you sure you want to logout?')) {
      await onLogout()
    }
  }

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      <aside className={`sidebar ${showSidebar ? 'visible' : 'hidden'}`}>
        <div className="sidebar-header">
          <h2>Chats</h2>
          <button
            type="button"
            onClick={handleNewChat}
            disabled={isCreatingThread}
            className="new-chat-button"
            title="New Chat"
            aria-label="Create new chat"
          >
            {isCreatingThread ? '...' : '+'}
          </button>
        </div>

        <div className="threads-list">
          {threads.length === 0 ? (
            <p className="no-threads">No chats yet. Start a new one!</p>
          ) : (
            threads.map((thread) => (
              <div
                key={thread.id}
                className={`thread-item ${activeThreadId === thread.id ? 'active' : ''}`}
                onClick={() => handleSelectThread(thread.id)}
              >
                {editingThreadId === thread.id ? (
                  <>
                    <input
                      type="text"
                      className="thread-title-input"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          handleSaveEditTitle(e, thread.id)
                        }
                        if (e.key === 'Escape') {
                          handleCancelEditTitle()
                        }
                      }}
                      autoFocus
                    />
                    <button
                      className="thread-action-button"
                      onClick={(e) => handleSaveEditTitle(e, thread.id)}
                      title="Save title"
                      aria-label="Save title"
                      disabled={isSavingTitle}
                    >
                      ✓
                    </button>
                    <button
                      className="thread-action-button"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleCancelEditTitle()
                      }}
                      title="Cancel editing"
                      aria-label="Cancel editing"
                      disabled={isSavingTitle}
                    >
                      ✕
                    </button>
                  </>
                ) : (
                  <>
                    <div className="thread-title">{thread.title}</div>
                    <button
                      className="thread-action-button"
                      onClick={(e) => handleStartEditTitle(e, thread.id, thread.title)}
                      title="Edit title"
                      aria-label="Edit title"
                    >
                      ✎
                    </button>
                    <button
                      className="delete-button"
                      onClick={(e) => handleDeleteThread(e, thread.id)}
                      title="Delete chat"
                      aria-label="Delete chat"
                    >
                      <svg
                        className="delete-icon"
                        viewBox="0 0 24 24"
                        fill="none"
                        xmlns="http://www.w3.org/2000/svg"
                      >
                        <path
                          d="M3 6h18M8 6V4h8v2m-9 0l1 14h8l1-14M10 10v7m4-7v7"
                          stroke="currentColor"
                          strokeWidth="1.8"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </button>
                  </>
                )}
              </div>
            ))
          )}
        </div>

        <div className="sidebar-footer">
          <button onClick={handleLogout} className="logout-button">
            Logout
          </button>
          <p className="user-email">{user?.email}</p>
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="chat-main">
        <div className="chat-header">
          <button
            className="toggle-sidebar-button"
            onClick={() => setShowSidebar(!showSidebar)}
            title="Toggle sidebar"
          >
            ☰
          </button>
          <div className="header-title">
            <h1>Amzur AI Chat</h1>
          </div>
        </div>

        <div className="messages-container" role="log" aria-live="polite">
          {error && <p className="error global-error">{error}</p>}
          {activeThreadId ? (
            <>
              {messages.length === 0 ? (
                <div className="empty-state">
                  <p>Start a conversation...</p>
                </div>
              ) : (
                messages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={`bubble ${message.role}`}>
                    <p className="role">{message.role === 'user' ? 'You' : 'Assistant'}</p>
                    <div className="content">
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>
                  </article>
                ))
              )}
              {isLoading && <p className="typing">Assistant is thinking...</p>}
            </>
          ) : (
            <div className="empty-state">
              <p>Create a new chat to get started</p>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <form className="composer" onSubmit={handleSendMessage}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={activeThreadId ? 'Type your question...' : 'Create a new chat first'}
            rows={3}
            disabled={isLoading || !activeThreadId}
            className="composer-textarea"
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim() || !activeThreadId}
            className="composer-button"
          >
            {isLoading ? 'Sending...' : 'Send'}
          </button>
        </form>
      </main>
    </div>
  )
}
