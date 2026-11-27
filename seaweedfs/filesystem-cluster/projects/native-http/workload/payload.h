#pragma once
#include <array>
#include <iomanip>
#include <memory>
#include <openssl/evp.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

std::vector<char> payload(const std::string &run, std::size_t seq,
                          std::size_t size) {
  auto seed = std::string("95513") + ":" + run + ":" + std::to_string(seq);
  std::vector<char> data(size);
  std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> ctx(EVP_MD_CTX_new(),
                                                              EVP_MD_CTX_free);
  if (!ctx || EVP_DigestInit_ex(ctx.get(), EVP_shake256(), nullptr) != 1 ||
      EVP_DigestUpdate(ctx.get(), seed.data(), seed.size()) != 1 ||
      EVP_DigestFinalXOF(
          ctx.get(), reinterpret_cast<unsigned char *>(data.data()), size) != 1)
    throw std::runtime_error("SHAKE256");
  return data;
}
std::string sha256(const std::vector<char> &data) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (EVP_Digest(data.data(), data.size(), digest.data(), &size, EVP_sha256(),
                 nullptr) != 1)
    throw std::runtime_error("SHA256");
  std::ostringstream out;
  for (unsigned int i = 0; i < size; ++i)
    out << std::hex << std::setw(2) << std::setfill('0') << unsigned(digest[i]);
  return out.str();
}
