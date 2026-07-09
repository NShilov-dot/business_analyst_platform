import { Component, ReactNode, ErrorInfo } from 'react'
import { Button } from '@/components/ui/button'

interface Props {
  children: ReactNode
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) console.error('[ErrorBoundary]', error, info.componentStack)
  }

  reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (error) {
      if (this.props.fallback) {
        return this.props.fallback(error, this.reset)
      }
      return (
        <div className="flex flex-col items-center justify-center py-24 gap-4">
          <p className="text-muted-foreground text-sm">Something went wrong.</p>
          <Button variant="outline" onClick={this.reset}>
            Try again
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
