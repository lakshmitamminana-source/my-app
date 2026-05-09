/**
 * Main chat interface with thread management.
 */
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import { useChat } from '../hooks/useChat'

export default function ChatPage({ user, onLogout }) {
  const markdownUrlTransform = (url) => {
    if (typeof url === 'string' && url.startsWith('data:image/')) {
      return url
    }
    return defaultUrlTransform(url)
  }

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
  const [pendingAttachments, setPendingAttachments] = useState([])
  const [isReadingAttachments, setIsReadingAttachments] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const messagesEndRef = useRef(null)
  const attachmentInputRef = useRef(null)
  const dragDepthRef = useRef(0)

  const codeExtensions = new Set([
    'js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'cs', 'go', 'rs', 'php', 'rb', 'swift',
    'kt', 'sql', 'sh', 'bash', 'yml', 'yaml', 'json', 'xml', 'html', 'css', 'scss', 'md', 'txt',
  ])
  const tableExtensions = new Set(['csv', 'tsv'])

  const readFileAsDataUrl = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        // Ensure result is always a string
        const result = reader.result
        if (typeof result === 'string') {
          resolve(result)
        } else {
          reject(new Error('Failed to read file as data URL'))
        }
      }
      reader.onerror = () => reject(new Error(`Could not read file: ${file.name}`))
      reader.readAsDataURL(file)
    })

  const readFileAsText = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        try {
          // Ensure result is always a string, not an object
          let text = reader.result
          console.log(`FileReader result type for ${file.name}:`, typeof text)
          
          if (typeof text !== 'string') {
            // Result is not a string - try to convert
            console.warn(`${file.name} FileReader result is ${typeof text}, converting...`)
            text = text ? String(text) : ''
          }
          
          // Additional safeguard: if somehow result object has toString() that returns [object Object]
          if (text === '[object Object]') {
            reject(new Error(`File ${file.name} could not be read properly - got [object Object]`))
            return
          }
          
          // Check if result is empty
          if (text === '' || text === null || text === undefined) {
            reject(new Error(`File ${file.name} is empty or could not be read`))
            return
          }
          
          console.log(`✓ Successfully read ${file.name}: ${text.length} characters`)
          resolve(text)
        } catch (err) {
          reject(new Error(`Error processing ${file.name}: ${err.message}`))
        }
      }
      reader.onerror = () => {
        console.error(`FileReader error for ${file.name}:`, reader.error)
        reject(new Error(`Could not read file: ${file.name}`))
      }
      reader.onabort = () => {
        reject(new Error(`Reading ${file.name} was aborted`))
      }
      reader.readAsText(file)
    })

  const buildAttachmentFromFile = async (file) => {
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : ''
    const base = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      name: file.name,
      mimeType: file.type || 'application/octet-stream',
    }

    if (file.type.startsWith('image/')) {
      const dataUrl = await readFileAsDataUrl(file)
      return { ...base, type: 'image', dataUrl }
    }

    if (file.type.startsWith('video/')) {
      const dataUrl = await readFileAsDataUrl(file)
      return { ...base, type: 'video', dataUrl }
    }

    // Helper to ensure textContent is valid
    const MAX_TEXT_CONTENT = 140000
    const validateTextContent = (text, filename) => {
      if (typeof text !== 'string') {
        throw new Error(`${filename}: textContent must be string, got ${typeof text}`)
      }
      if (!text || text.trim() === '') {
        throw new Error(`${filename}: textContent is empty`)
      }
      if (text.length > MAX_TEXT_CONTENT) {
        return text.slice(0, MAX_TEXT_CONTENT) + `\n\n[Content truncated — showing first ${MAX_TEXT_CONTENT.toLocaleString()} of ${text.length.toLocaleString()} characters]`
      }
      return text
    }

    // Handle table/spreadsheet files
    if (tableExtensions.has(ext) || ext === 'xls' || ext === 'xlsx') {
      const textContent = await readFileAsText(file)
      return { ...base, type: 'table', textContent: validateTextContent(textContent, file.name) }
    }

    // Handle text, code, and other documents
    if (file.type.startsWith('text/') || codeExtensions.has(ext) || ext === 'txt') {
      const textContent = await readFileAsText(file)
      return {
        ...base,
        type: 'code',
        language: ext || 'text',
        textContent: validateTextContent(textContent, file.name),
      }
    }

    // Handle PDF documents - extract text on backend
    if (ext === 'pdf' || file.type.includes('pdf')) {
      try {
        // Send PDF to backend for text extraction
        const formData = new FormData()
        formData.append('file', file)
        
        const response = await fetch('/api/chat/extract-pdf', {
          method: 'POST',
          credentials: 'include',
          body: formData,
        })
        
        if (!response.ok) {
          throw new Error(`PDF extraction failed: ${response.statusText}`)
        }
        
        const result = await response.json()
        return {
          ...base,
          type: 'pdf',
          language: 'pdf',
          textContent: validateTextContent(result.text, file.name),
        }
      } catch (err) {
        console.error('PDF extraction error:', err)
        throw new Error(`Could not extract PDF ${file.name}: ${err.message}`)
      }
    }

    // Fallback: treat unknown files as code/document attachments
    if (file.size < 5 * 1024 * 1024) {
      // If file is < 5MB, try to read as text
      try {
        const textContent = await readFileAsText(file)
        return {
          ...base,
          type: 'code',
          language: ext || 'text',
          textContent: validateTextContent(textContent, file.name),
        }
      } catch (textErr) {
        // If text reading fails, try data URL
        try {
          const dataUrl = await readFileAsDataUrl(file)
          const textContent = `[${file.name}] Binary file - ${(file.size / 1024).toFixed(2)}KB`
          return {
            ...base,
            type: 'code',
            language: ext || 'binary',
            textContent: validateTextContent(textContent, file.name),
            dataUrl,
          }
        } catch (dataErr) {
          throw new Error(`Could not read file ${file.name}: ${dataErr.message}`)
        }
      }
    }

    throw new Error(`File too large: ${file.name}. Maximum size is 5MB.`)
  }

  const handleAddFiles = async (fileList) => {
    const files = Array.from(fileList || [])
    if (!files.length) return

    setIsReadingAttachments(true)
    const nextAttachments = []
    const failedFiles = []

    for (const file of files) {
      try {
        console.log(`Reading file: ${file.name}, type: ${file.type}, size: ${file.size}`)
        const attachment = await buildAttachmentFromFile(file)
        console.log(`✓ Attachment created:`, { 
          name: attachment.name, 
          type: attachment.type, 
          textContentType: typeof attachment.textContent,
          hasTextContent: !!attachment.textContent
        })
        nextAttachments.push(attachment)
      } catch (err) {
        console.error(`✗ Failed to read ${file.name}:`, err.message)
        failedFiles.push(`${file.name} (${err.message})`)
      }
    }

    if (nextAttachments.length) {
      setPendingAttachments((prev) => [...prev, ...nextAttachments])
    }

    if (failedFiles.length) {
      setError(`Some files were skipped: ${failedFiles.join(', ')}`)
    }

    setIsReadingAttachments(false)
  }

  const handleAttachmentInputChange = async (e) => {
    await handleAddFiles(e.target.files)
    e.target.value = ''
  }

  const handlePasteIntoComposer = async (e) => {
    const clipboardItems = Array.from(e.clipboardData?.items || [])
    const pastedFiles = clipboardItems
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter(Boolean)

    if (!pastedFiles.length) {
      return
    }

    e.preventDefault()
    await handleAddFiles(pastedFiles)
  }

  const handleComposerDragEnter = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    dragDepthRef.current += 1
    setIsDragActive(true)
  }

  const handleComposerDragOver = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const handleComposerDragLeave = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) {
      setIsDragActive(false)
    }
  }

  const handleComposerDrop = async (e) => {
    if (!activeThreadId || isLoading) return

    e.preventDefault()
    dragDepthRef.current = 0
    setIsDragActive(false)

    const droppedFiles = Array.from(e.dataTransfer?.files || [])
    if (!droppedFiles.length) return

    await handleAddFiles(droppedFiles)
  }

  const handleRemoveAttachment = (attachmentId) => {
    setPendingAttachments((prev) => prev.filter((attachment) => attachment.id !== attachmentId))
  }

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
    if ((!input.trim() && pendingAttachments.length === 0) || isLoading || !activeThreadId) return

    const userMessage = input.trim() || 'Shared attachments'
    const outgoingAttachments = [...pendingAttachments]
    setInput('')
    setPendingAttachments([])

    try {
      // Convert messages to proper format for API
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))

        await sendMessage(userMessage, history, outgoingAttachments)
    } catch (err) {
      setPendingAttachments(outgoingAttachments)
      setError(err instanceof Error ? err.message : 'Failed to send message')
    }
  }

  const handleLogout = async () => {
    if (window.confirm('Are you sure you want to logout?')) {
      await onLogout()
    }
  }

  const parseCSV = (csvText) => {
    // Handle both string and object inputs
    let text = csvText
    if (typeof csvText === 'object' && csvText !== null) {
      try {
        text = JSON.stringify(csvText, null, 2)
      } catch (e) {
        text = String(csvText)
      }
    } else {
      text = String(csvText || '')
    }
    
    const lines = text.split('\n').filter((line) => line.trim())
    if (lines.length === 0) return { headers: [], rows: [] }

    // Simple CSV parser (handles basic cases, not all edge cases)
    const rows = lines.map((line) =>
      line.split(',').map((cell) => cell.trim())
    )

    return {
      headers: rows[0] || [],
      rows: rows.slice(1),
    }
  }

  const renderAttachmentPreview = (attachment) => {
    try {
      const dataUrl = attachment.dataUrl || attachment.data_url
      const mimeType = attachment.mimeType || attachment.mime_type
      let textContent = attachment.textContent || attachment.text_content
      
      // Debug logging for [object Object] issues
      console.log(`Rendering attachment ${attachment.name} (type=${attachment.type}):`, {
        textContentType: typeof textContent,
        textContentValue: textContent ? String(textContent).substring(0, 50) : 'EMPTY',
        hasTextContent: !!textContent,
      })
      
      // Ensure textContent is always a string (handle object responses from API)
      if (textContent && typeof textContent !== 'string') {
        console.warn(`Converting non-string textContent for ${attachment.name}`)
        if (typeof textContent === 'object') {
          try {
            textContent = JSON.stringify(textContent, null, 2)
          } catch (e) {
            textContent = String(textContent)
          }
        } else {
          textContent = String(textContent)
        }
      } else if (!textContent) {
        textContent = ''
      }

      if (attachment.type === 'image' && dataUrl) {
        return <img src={dataUrl} alt={attachment.name || 'Image attachment'} className="attachment-image" />
      }

      if (attachment.type === 'video' && dataUrl) {
        return (
          <video className="attachment-video" controls preload="metadata">
            <source src={dataUrl} type={mimeType || 'video/mp4'} />
            Your browser does not support this video preview.
          </video>
        )
      }

      if (attachment.type === 'table') {
        const csvContent = String(textContent)
        const { headers, rows } = parseCSV(csvContent)
        
        // Check if parsing was successful
        const hasValidHeaders = headers.length > 0 && headers.some((h) => h.trim())
        
        if (!hasValidHeaders) {
          // Fallback to pre if parsing fails
          return (
            <pre className="attachment-code-block" style={{ maxHeight: '300px', overflow: 'auto' }}>
              {csvContent}
            </pre>
          )
        }
        
        return (
          <div className="attachment-table-container">
            <table className="attachment-table">
              <thead>
                <tr>
                  {headers.map((header, i) => (
                    <th key={i}>{header || `Col ${i}`}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows && rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row && row.map((cell, cellIdx) => (
                      <td key={cellIdx}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }

      if (attachment.type === 'formula') {
        return <pre className="attachment-code-block">{`$$${String(textContent || '')}$$`}</pre>
      }

      if (attachment.type === 'code') {
        return (
          <pre className="attachment-code-block">
            <code>{String(textContent || '')}</code>
          </pre>
        )
      }

      if (attachment.type === 'pdf') {
        return (
          <pre className="attachment-code-block">
            <code>{String(textContent || '')}</code>
          </pre>
        )
      }

      console.warn(`Unknown attachment type: ${attachment.type}`)
      return null
    } catch (err) {
      console.error(`Error rendering attachment ${attachment?.name}:`, err)
      return (
        <div className="attachment-error">
          <p>Error displaying attachment: {err.message}</p>
          <p>Type: {attachment?.type}</p>
        </div>
      )
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
          <div className="header-user-block" title={user?.email || ''}>
            <span className="header-welcome">Welcome,</span>
            <span className="header-user-email">{user?.email}</span>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="logout-icon-button"
            title="Logout"
            aria-label="Logout"
          >
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path
                d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 16l4-4m0 0l-4-4m4 4H9"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
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
                      <ReactMarkdown urlTransform={markdownUrlTransform}>{message.content}</ReactMarkdown>
                    </div>
                    {Array.isArray(message.attachments) && message.attachments.length > 0 ? (
                      <div className="message-attachments">
                        {message.attachments.map((attachment) => (
                          <div key={attachment.id || `${attachment.type}-${attachment.name}`} className="attachment-card">
                            <p className="attachment-label">{attachment.type.toUpperCase()}</p>
                            {renderAttachmentPreview(attachment)}
                          </div>
                        ))}
                      </div>
                    ) : null}
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

        <form
          className={`composer ${isDragActive ? 'drag-active' : ''}`}
          onSubmit={handleSendMessage}
          onDragEnter={handleComposerDragEnter}
          onDragOver={handleComposerDragOver}
          onDragLeave={handleComposerDragLeave}
          onDrop={handleComposerDrop}
        >
          <input
            ref={attachmentInputRef}
            type="file"
            multiple
            className="attachment-input"
            accept="image/*,video/*,.csv,.tsv,.txt,.pdf,.xls,.xlsx,.md,.json,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.java,.c,.cpp,.cs,.go,.rs,.php,.rb,.swift,.kt,.sql,.sh,.yaml,.yml"
            onChange={handleAttachmentInputChange}
          />
          <button
            type="button"
            className="attach-files-icon-button"
            onClick={() => attachmentInputRef.current?.click()}
            disabled={isLoading || !activeThreadId || isReadingAttachments}
            title={isReadingAttachments ? 'Reading attachments...' : 'Attach files'}
            aria-label="Attach files"
          >
            +
          </button>

          {pendingAttachments.length > 0 ? (
            <div className="pending-attachments">
              {pendingAttachments.map((attachment) => (
                <div key={attachment.id} className="pending-attachment-chip">
                  <span>{attachment.type.toUpperCase()}: {attachment.name || 'inline-content'}</span>
                  <button
                    type="button"
                    className="remove-attachment-button"
                    onClick={() => handleRemoveAttachment(attachment.id)}
                    aria-label={`Remove ${attachment.name || attachment.type} attachment`}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className="composer-input-row">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={handlePasteIntoComposer}
              placeholder={activeThreadId ? 'Type your question...' : 'Create a new chat first'}
              rows={3}
              disabled={isLoading || !activeThreadId}
              className="composer-textarea"
            />
            <button
              type="submit"
              disabled={
                isLoading ||
                (!input.trim() && pendingAttachments.length === 0) ||
                !activeThreadId ||
                isReadingAttachments
              }
              className="composer-button"
            >
              {isLoading ? 'Sending...' : 'Send'}
            </button>
          </div>
          {isDragActive ? <p className="drop-help-text">Drop files here to attach</p> : null}
        </form>
      </main>
    </div>
  )
}
