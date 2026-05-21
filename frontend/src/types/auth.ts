export interface SessionUser {
  authenticated: boolean
  name: string
  email: string
  role: string
  token: string
}

export interface LoginResponse {
  access_token: string
  id_token: string
  refresh_token: string
  user: {
    user_id: string
    email: string
    name: string
    role: string
  }
}

export const emptySessionUser = (): SessionUser => ({
  authenticated: false,
  name: '',
  email: '',
  role: '',
  token: '',
})

export function welcomeMessage(user: SessionUser): string {
  return `Welcome ${user.name}.`
}
