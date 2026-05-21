import {
  createApi,
  fetchBaseQuery,
  type BaseQueryFn,
  type FetchArgs,
  type FetchBaseQueryError,
} from '@reduxjs/toolkit/query/react'
import {
  connectionErrorMessage,
  getApiBaseUrl,
  parseApiErrorBody,
} from '../../lib/apiError'
import {
  AUTH_STORAGE_KEY,
  expireStoredAuthSession,
  isExpiredTokenMessage,
} from '../../lib/authSession'

const rawBaseQuery = fetchBaseQuery({
  baseUrl: getApiBaseUrl(),
  prepareHeaders: (headers) => {
    headers.set('X-Request-Time', new Date().toISOString())

    try {
      const raw = localStorage.getItem(AUTH_STORAGE_KEY)
      if (!raw) return headers
      const user = JSON.parse(raw) as { token?: string }
      if (!user?.token) return headers
      const authVal = `Bearer ${user.token}`
      headers.set('Authorization', authVal)
      headers.set('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization', authVal)
    } catch {
      /* ignore corrupt storage */
    }

    return headers
  },
})

const baseQueryWithErrors: BaseQueryFn<
  string | FetchArgs,
  unknown,
  FetchBaseQueryError | { status: 'CUSTOM_ERROR'; error: string }
> = async (args, api, extraOptions) => {
  try {
    const result = await rawBaseQuery(args, api, extraOptions)
    if (result.error) {
      if (result.error.status === 'FETCH_ERROR') {
        return {
          error: {
            status: 'CUSTOM_ERROR' as const,
            error: connectionErrorMessage(result.error.error),
          },
        }
      }
      const status =
        typeof result.error.status === 'number' ? result.error.status : 0
      const statusText =
        status === 502
          ? 'Bad Gateway'
          : status === 504
            ? 'Gateway Timeout'
            : status === 503
              ? 'Service Unavailable'
              : ''
      const message = parseApiErrorBody(result.error.data, status, statusText)
      if (status === 401 && isExpiredTokenMessage(message)) {
        expireStoredAuthSession()
      }
      return { error: { status: 'CUSTOM_ERROR' as const, error: message } }
    }
    return result
  } catch (error) {
    return {
      error: {
        status: 'CUSTOM_ERROR' as const,
        error: connectionErrorMessage(error),
      },
    }
  }
}

export const baseApi = createApi({
  reducerPath: 'api',
  baseQuery: baseQueryWithErrors,
  tagTypes: ['Session'],
  endpoints: () => ({}),
})

export const API_BASE = getApiBaseUrl()
