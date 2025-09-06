package internal

func Map[T, V any](ts []T, fn func(T) V) []V {
	result := make([]V, len(ts))
	for i, t := range ts {
		result[i] = fn(t)
	}
	return result
}

func FirstN(str string, n int) string {
	if n >= len(str) {
		return str
	}
	return str[:n]
}
