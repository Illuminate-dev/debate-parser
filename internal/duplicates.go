package internal

import (
	"crypto/md5"
	"fmt"
	"math"
	"regexp"
	"strings"
)

// Duplicate detection file

var ShingleSize = 3

type MinHashLSH struct {
	numHashes   int
	numBands    int
	rowsPerBand int
	buckets     map[string][]string
	signatures  map[string][]uint32
}

func NewMinHashLSH(numHashes, numBands int) *MinHashLSH {
	return &MinHashLSH{
		numHashes,
		numBands,
		numHashes / numBands,
		make(map[string][]string),
		make(map[string][]uint32),
	}
}

func tokenize(text string, shingleSize int) []string {
	reg := regexp.MustCompile(`[^\w\s]`)
	clean := strings.ToLower(reg.ReplaceAllString(text, ""))
	words := strings.Fields(clean)

	var shingles []string
	for i := 0; i <= len(words)-shingleSize; i++ {
		shingle := strings.Join(words[i:i+shingleSize], " ")
		shingles = append(shingles, shingle)
	}

	return shingles
}

func hashString(s string, seed uint32) uint32 {
	hash := md5.Sum([]byte(s + fmt.Sprint(seed)))
	result := uint32(hash[0]) | uint32(hash[1])<<8 | uint32(hash[2])<<16 | uint32(hash[3])<<24
	return result
}

func (lsh *MinHashLSH) ComputeMinHash(docID, text string, shingleSize int) {
	tokens := tokenize(text, shingleSize)
	signature := make([]uint32, lsh.numHashes)

	for i := range signature {
		signature[i] = math.MaxUint32
	}

	for _, token := range tokens {
		for i := 0; i < lsh.numHashes; i++ {
			hash := hashString(token, uint32(i))
			if hash < signature[i] {
				signature[i] = hash
			}
		}
	}

	lsh.signatures[docID] = signature
	lsh.addToBuckets(docID, signature)
}

func (lsh *MinHashLSH) addToBuckets(docID string, signature []uint32) {
	for band := 0; band < lsh.numBands; band++ {
		start := band * lsh.rowsPerBand
		end := start + lsh.rowsPerBand

		bandSig := signature[start:end]
		bandHash := fmt.Sprintf("%v", bandSig)

		lsh.buckets[bandHash] = append(lsh.buckets[bandHash], docID)
	}
}

func (lsh *MinHashLSH) FindCandidates(docID string) []string {
	signature, exists := lsh.signatures[docID]
	if !exists {
		return nil
	}

	candidateSet := make(map[string]bool)
	for band := 0; band < lsh.numBands; band++ {
		start := band * lsh.rowsPerBand
		end := start + lsh.rowsPerBand

		bandSig := signature[start:end]
		bandHash := fmt.Sprintf("%v", bandSig)

		for _, candidate := range lsh.buckets[bandHash] {
			if candidate != docID {
				candidateSet[candidate] = true
			}
		}
	}

	var candidates []string
	for candidate := range candidateSet {
		candidates = append(candidates, candidate)
	}

	return candidates
}

func (lsh *MinHashLSH) CheckForDuplicates(text string, shingleSize int) []string {
	tokens := tokenize(text, shingleSize)
	signature := make([]uint32, lsh.numHashes)

	for i := range signature {
		signature[i] = math.MaxUint32
	}

	for _, token := range tokens {
		for i := 0; i < lsh.numHashes; i++ {
			hash := hashString(token, uint32(i))
			if hash < signature[i] {
				signature[i] = hash
			}
		}
	}

	candidateSet := make(map[string]bool)
	for band := 0; band < lsh.numBands; band++ {
		start := band * lsh.rowsPerBand
		end := start + lsh.rowsPerBand

		bandSig := signature[start:end]
		bandHash := fmt.Sprintf("%v", bandSig)

		for _, candidate := range lsh.buckets[bandHash] {
			candidateSet[candidate] = true
		}
	}

	var candidates []string
	for candidate := range candidateSet {
		candidates = append(candidates, candidate)
	}

	return candidates
}

func (lsh *MinHashLSH) EstimateJaccardSimilarity(docID1, docID2 string) float64 {
	sig1, exists1 := lsh.signatures[docID1]
	sig2, exists2 := lsh.signatures[docID2]

	if !exists1 || !exists2 {
		return 0.0
	}

	matches := 0
	for i := range len(sig1) {
		if sig1[i] == sig2[i] {
			matches++
		}
	}

	return float64(matches) / float64(len(sig1))
}

func (lsh *MinHashLSH) EstimateJaccardSimilarityWithText(docID, text string, shingleSize int) float64 {
	sig1, exists := lsh.signatures[docID]
	if !exists {
		return 0.0
	}

	sig2 := lsh.computeSignature(text, shingleSize)

	matches := 0
	for i := range len(sig1) {
		if sig1[i] == sig2[i] {
			matches++
		}
	}

	return float64(matches) / float64(len(sig1))
}

func (lsh *MinHashLSH) computeSignature(text string, shingleSize int) []uint32 {
	tokens := tokenize(text, shingleSize)
	signature := make([]uint32, lsh.numHashes)

	for i := range signature {
		signature[i] = math.MaxUint32
	}

	for _, token := range tokens {
		for i := 0; i < lsh.numHashes; i++ {
			hash := hashString(token, uint32(i))
			if hash < signature[i] {
				signature[i] = hash
			}
		}
	}

	return signature
}
