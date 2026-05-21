import type { LoginResponse } from '../../types/auth'
import { baseApi } from './baseApi'

export interface AuthMessageResponse {
  message: string
}

export interface SignupRequest {
  name: string
  email: string
  password: string
  role: string
}

export interface ConfirmSignupRequest {
  email: string
  confirmation_code: string
}

export interface LoginRequest {
  email: string
  password: string
}

export const authApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    login: builder.mutation<LoginResponse, LoginRequest>({
      query: (body) => ({
        url: '/auth/login',
        method: 'POST',
        body,
      }),
    }),
    signup: builder.mutation<AuthMessageResponse, SignupRequest>({
      query: (body) => ({
        url: '/auth/signup',
        method: 'POST',
        body,
      }),
    }),
    confirmSignup: builder.mutation<AuthMessageResponse, ConfirmSignupRequest>({
      query: (body) => ({
        url: '/auth/confirm',
        method: 'POST',
        body,
      }),
    }),
  }),
})

export const {
  useLoginMutation,
  useSignupMutation,
  useConfirmSignupMutation,
} = authApi
